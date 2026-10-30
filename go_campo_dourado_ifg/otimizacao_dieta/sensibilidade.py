"""
Análise de sensibilidade global de Sobol sobre a dieta ótima.

Cada amostra sorteia preços dos ingredientes, características da vaca e o preço do leite, resolve a PL inteira e
registra o custo mínimo, o IOFC e o custo alimentar por kg de leite. Os índices de Sobol dizem quanto da variância de
cada resultado vem de cada fator:

* S1 (primeira ordem): efeito do fator sozinho;
* ST (total): efeito do fator incluindo interações com os outros. ST − S1 grande indica interação.

Amostragem de Saltelli/Sobol (SALib ``sample.sobol``) e análise ``analyze.sobol``.

Cada dieta é conferida nas restrições do RuFaS e cada amostra registra se a SLP convergiu. O estimador de Saltelli
precisa de todas as amostras, então os índices principais usam todas; as colunas ``*_validas`` repetem a conta dando às
amostras cuja dieta não atende o RuFaS a média das que atendem, para mostrar quanto elas pesam.
"""

from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import pandas as pd
from SALib.analyze import sobol as sobol_analise
from SALib.sample import sobol as sobol_amostra

from .biblioteca import carregar_alimentos
from .config import ConfigOtimizacao, Lote, carregar_config
from .modelo import otimizar_lote

SAIDAS = ["custo_dieta_brl", "iofc_brl", "custo_kg_leite_brl"]
# Exigência que serve de escala para a folga de cada restrição do RuFaS (folga em % da exigência).
ESCALA_FOLGA = {"NASEM_net_energy_constraint": "nel_mcal", "protein_constraint_lower": "pm_g",
                "calcium_constraint": "ca_g", "phosphorus_constraint": "p_g"}


@dataclass(frozen=True)
class Fator:
    nome: str
    tipo: str            # "preco", "animal" ou "preco_leite"
    chave: str | int     # rufas_id para preço, campo do Lote para animal
    minimo: float
    maximo: float
    base: float
    rotulo: str


def fatores_da_config(config: ConfigOtimizacao) -> list[Fator]:
    s = config.sensibilidade
    lote = config.lote(s["lote"])
    var = s.get("variacao_preco_pct", 25) / 100
    fatores = [
        Fator(f"preco_{i.rufas_id}", "preco", i.rufas_id, i.preco_brl_kg_ms * (1 - var),
              i.preco_brl_kg_ms * (1 + var), i.preco_brl_kg_ms, f"Preço {i.nome}")
        for i in config.ingredientes
    ]
    rotulos = {"leite_kg": "Produção de leite", "peso_vivo": "Peso vivo", "del": "Dias em lactação",
               "gordura_pct": "Gordura do leite", "proteina_pct": "Proteína do leite", "parto": "Ordem de parto"}
    for f in s.get("fatores_animais", []):
        campo = "del_" if f["nome"] == "del" else f["nome"]
        fatores.append(Fator(f["nome"], "animal", f["nome"], f["min"], f["max"], getattr(lote, campo),
                             rotulos.get(f["nome"], f["nome"])))
    if "fator_preco_leite" in s:
        f = s["fator_preco_leite"]
        fatores.append(Fator("preco_leite", "preco_leite", "preco_leite", f["min"], f["max"],
                             config.preco_leite_brl_kg, "Preço do leite"))
    return fatores


# --------------------------------------------------------------------------------------------- avaliação do modelo
_ESTADO: dict[str, object] = {}


def _inicializar(caminho_config: str, lote_id: str, nomes: list[str]) -> None:
    config = carregar_config(caminho_config)
    alimentos = carregar_alimentos(config)
    lote = config.lote(lote_id)
    base = otimizar_lote(config, lote, alimentos, validar=False)
    _ESTADO.update(config=config, alimentos=alimentos, lote=lote, fatores=fatores_da_config(config),
                   x0=np.array(list(base.kg_ms.values())))


def aplicar_fatores(valores, config: ConfigOtimizacao, alimentos, lote: Lote, fatores: list[Fator]):
    """Alimentos com os preços, lote com as características e preço do leite de um vetor de valores dos fatores."""
    precos, mudancas_lote, preco_leite = {}, {}, config.preco_leite_brl_kg
    for fator, valor in zip(fatores, valores):
        if fator.tipo == "preco":
            precos[fator.chave] = float(valor)
        elif fator.tipo == "animal":
            mudancas_lote[fator.chave] = int(round(valor)) if fator.chave in ("del", "parto") else float(valor)
        else:
            preco_leite = float(valor)
    alimentos_amostra = [replace(a, purchase_cost=precos.get(a.rufas_id, a.purchase_cost)) for a in alimentos]
    return alimentos_amostra, lote.com(**mudancas_lote), preco_leite


def avaliar(valores: np.ndarray, config: ConfigOtimizacao, alimentos, lote: Lote, fatores: list[Fator],
            x0: np.ndarray | None = None) -> dict[str, float | bool | str]:
    """
    Resolve a PL para um vetor de valores dos fatores e confere a dieta nas restrições do RuFaS.

    Retorna custo, IOFC, custo/kg leite, redução de leite, se a SLP convergiu, se a dieta atende o RuFaS e a menor folga
    (restrição, valor na unidade dela e em % da exigência, quando a restrição tem uma).
    """
    alimentos_amostra, lote_amostra, preco_leite = aplicar_fatores(valores, config, alimentos, lote, fatores)
    r = otimizar_lote(config, lote_amostra, alimentos_amostra, x_inicial=x0, validar=True)
    if not r.viavel:
        return {s: float("nan") for s in SAIDAS} | {"reducao_leite_kg": float("nan"), "convergiu_slp": False,
                                                    "atende_rufas": False}
    receita = r.leite_atendido_kg * preco_leite
    pior = min(r.validacao_rufas, key=r.validacao_rufas.get)
    escala = ESCALA_FOLGA.get(pior)
    return {
        "custo_dieta_brl": r.custo_brl, "iofc_brl": receita - r.custo_brl,
        "custo_kg_leite_brl": r.custo_brl / max(r.leite_atendido_kg, 1e-9), "reducao_leite_kg": r.reducao_leite_kg,
        "convergiu_slp": r.convergiu_slp, "iteracoes_slp": r.iteracoes_slp, "atende_rufas": r.validacao_ok,
        "restricao_menor_folga": pior, "menor_folga": r.validacao_rufas[pior],
        "menor_folga_pct_exigencia": 100 * r.validacao_rufas[pior] / r.resumo_exigencias[escala] if escala
        else float("nan"),
    }


def _avaliar_bloco(bloco: np.ndarray) -> list[dict]:
    e = _ESTADO
    return [avaliar(v, e["config"], e["alimentos"], e["lote"], e["fatores"], e["x0"]) for v in bloco]


def _indices_sobol(problema: dict, y: np.ndarray, semente: int) -> dict:
    if np.isnan(y).any():  # amostras sem valor recebem a média, para não quebrar o estimador
        y = np.where(np.isnan(y), np.nanmean(y), y)
    return sobol_analise.analyze(problema, y, calc_second_order=False, seed=semente)


def rodar_sobol(config: ConfigOtimizacao, n_base: int | None = None, processos: int | None = None,
                destino: Path | None = None) -> dict[str, object]:
    s = config.sensibilidade
    n_base = n_base or int(s.get("n_base", 256))
    fatores = fatores_da_config(config)
    problema = {"num_vars": len(fatores), "names": [f.nome for f in fatores],
                "bounds": [[f.minimo, f.maximo] for f in fatores]}
    amostras = sobol_amostra.sample(problema, n_base, calc_second_order=False, seed=s.get("semente", 44))

    processos = processos or max(1, (os.cpu_count() or 2) - 1)
    blocos = np.array_split(amostras, processos * 4)
    with ProcessPoolExecutor(max_workers=processos, initializer=_inicializar,
                             initargs=(str(config.caminho), s["lote"], problema["names"])) as executor:
        resultados = [linha for bloco in executor.map(_avaliar_bloco, blocos) for linha in bloco]

    tabela = pd.concat([pd.DataFrame(amostras, columns=problema["names"]), pd.DataFrame(resultados)], axis=1)
    invalidas = int(tabela[SAIDAS].isna().any(axis=1).sum())
    resolvidas = tabela[~tabela[SAIDAS].isna().any(axis=1)]
    violadas = resolvidas[~resolvidas.atende_rufas.astype(bool)]
    controle = {
        "nao_convergiram": int((~resolvidas.convergiu_slp.astype(bool)).sum()),
        "nao_atendem_rufas": len(violadas),
        "nao_atendem_rufas_e_nao_convergiram": int((~violadas.convergiu_slp.astype(bool)).sum()),
        "restricoes_violadas": violadas.restricao_menor_folga.value_counts().to_dict(),
        "pior_folga_pct_exigencia": float(violadas.menor_folga_pct_exigencia.min()) if len(violadas) else 0.0,
        "mediana_folga_pct_exigencia": float(violadas.menor_folga_pct_exigencia.median()) if len(violadas) else 0.0,
    }

    # SALib 1.5.1 chama pd.unique(lista de nomes), que o pandas recente não aceita; ndarray contorna.
    problema_analise = dict(problema, names=np.array(problema["names"]))
    semente = s.get("semente", 44)
    atende = tabela.atende_rufas.astype(bool).to_numpy()
    indices = []
    for saida in SAIDAS:
        y = tabela[saida].to_numpy(dtype=float)
        si = _indices_sobol(problema_analise, y, semente)
        si_validas = _indices_sobol(problema_analise, np.where(atende, y, np.nan), semente)
        for k, f in enumerate(fatores):
            indices.append({"saida": saida, "fator": f.nome, "rotulo": f.rotulo, "S1": si["S1"][k],
                            "S1_conf": si["S1_conf"][k], "ST": si["ST"][k], "ST_conf": si["ST_conf"][k],
                            "S1_validas": si_validas["S1"][k], "ST_validas": si_validas["ST"][k]})
    indices = pd.DataFrame(indices)

    if destino is not None:
        destino.mkdir(parents=True, exist_ok=True)
        tabela.to_csv(destino / "sobol_amostras.csv", index=False)
        indices.to_csv(destino / "sobol_indices.csv", index=False)
        pd.DataFrame([f.__dict__ for f in fatores]).to_csv(destino / "sobol_fatores.csv", index=False)
    return {"fatores": fatores, "amostras": tabela, "indices": indices, "n_avaliacoes": len(tabela),
            "invalidas": invalidas, "n_base": n_base, "controle": controle}


def fatores_mais_influentes(indices: pd.DataFrame, k: int, saida: str = "iofc_brl") -> list[str]:
    ordem = indices[indices.saida == saida].sort_values("ST", ascending=False)
    return ordem.fator.head(k).tolist()
