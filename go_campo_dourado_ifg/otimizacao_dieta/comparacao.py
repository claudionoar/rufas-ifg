"""
Comparação da PL do módulo com o otimizador original do RuFaS (``RationOptimizer.attempt_optimization``, SLSQP).

Os dois resolvem o mesmo problema: as mesmas exigências NASEM (calculadas pelo RuFaS), os mesmos alimentos, preços e
limites de inclusão, e as perdas de energia em metano e N urinário fixas na CMS prevista, que é como o RationOptimizer
as recebe na simulação. O SLSQP parte de um ponto sorteado, então cada caso roda com várias sementes. Vale a execução
mais barata entre as que terminam sem violação relevante (folga ≥ −TOLERANCIA_VIOLACAO nas restrições do RuFaS),
preferindo as que o SciPy declarou convergidas, que é o critério com que o RuFaS aceita a dieta na simulação.

Casos: o caso de referência da aula (vaca de 740 kg, ração e preços em US$ do GO_campo_dourado original), os lotes da
fazenda e os cenários pairwise da vaca referência.
"""

from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from RUFAS.data_structures.feed_storage_to_animal_connection import NASEMFeed

from .biblioteca import caminho_biblioteca, carregar_alimentos, criar_feed, ler_tabela
from .config import ConfigOtimizacao, Lote
from .modelo import _coeficientes_no_ponto, montar_problema, otimizar_com_slsqp_rufas, otimizar_lote, validar_com_rufas
from .sensibilidade import aplicar_fatores, fatores_da_config

# Vaca do slide "Caso de referência: o custo mínimo" da aula.
VACA_DA_AULA = {"peso_vivo": 740.0, "peso_adulto": 740.1, "del_": 150, "leite_kg": 35.0, "gordura_pct": 4.0,
                "proteina_pct": 3.2, "parto": 2, "ecc": 3.0, "dias_gestacao": None}

Caso = tuple[str, str, Lote, list[NASEMFeed]]  # (grupo, nome, lote, alimentos)
# Folga mínima (na unidade de cada restrição: g, Mcal, kg) para uma dieta do SLSQP contar como viável.
TOLERANCIA_VIOLACAO = 1e-3


def caso_da_aula(config: ConfigOtimizacao) -> Caso:
    """A vaca do slide com a ração lac_cow original do GO_campo_dourado: limites da biblioteca, preços em US$/kg MS."""
    pasta = config.dir_fazenda / "inputs_resolvidos"
    arquivo = pasta / "feed_original.json" if (pasta / "feed_original.json").exists() else pasta / "feed.json"
    feed = json.loads(arquivo.read_text(encoding="utf-8"))
    precos = {f["feed_type"]: f["purchased_feed_cost"] for f in feed["feeds"]}
    ids = sorted({f["feed_type"] for r in feed["rations"] if r["animal_combination"] == "lac_cow" for f in r["feeds"]})
    tabela = ler_tabela(caminho_biblioteca(config))
    alimentos = [replace(criar_feed(tabela.loc[i]), purchase_cost=precos[i], on_farm_cost=precos[i]) for i in ids]
    lote = config.lote(config.sensibilidade.get("lote", config.lotes[-1].id))
    return "aula", "Caso da aula (US$)", lote.com(id="aula", nome="Caso da aula (US$)", **VACA_DA_AULA), alimentos


def casos_pairwise(config: ConfigOtimizacao, alimentos: list[NASEMFeed]) -> list[Caso]:
    """Os cenários de ``pairwise_cenarios.csv`` (preços e características da vaca referência)."""
    arquivo = config.dir_etapa("pairwise") / "pairwise_cenarios.csv"
    if not arquivo.exists():
        return []
    fatores = fatores_da_config(config)
    lote = config.lote(config.sensibilidade["lote"])
    casos = []
    for linha in pd.read_csv(arquivo).to_dict("records"):
        valores = [linha.get(f"{f.nome}_valor", f.base) for f in fatores]
        alimentos_cenario, lote_cenario, _ = aplicar_fatores(valores, config, alimentos, lote, fatores)
        casos.append(("pairwise", f"Pairwise {linha['cenario']}", lote_cenario, alimentos_cenario))
    return casos


def dieta_atende_linhas_da_pl(config: ConfigOtimizacao, lote: Lote, exigencias: dict[str, float],
                              alimentos: list[NASEMFeed], x: np.ndarray) -> bool:
    """
    Verifica se a dieta ``x`` atende todas as linhas da PL linearizada nela mesma.

    Se a dieta do RationOptimizer atende e é mais barata, a formulação está certa e a diferença vem da SLP, que parou
    num ponto fixo mais caro (ela trata os coeficientes do ponto como constantes e ignora como mudam com a dieta).
    """
    p = config.parametros
    coef = _coeficientes_no_ponto(alimentos, x, lote.peso_vivo, p, exigencias["cms_kg"])
    xa = np.append(x, min(float(coef["pmic_ndt"] @ x), float(coef["pmic_pdr"] @ x)))
    for r in montar_problema(alimentos, exigencias, coef, p).restricoes:
        valor = float(r.coeficientes @ xa)
        folga = valor - r.lado_direito if r.sentido == ">=" else r.lado_direito - valor
        if folga < -1e-6 * max(1.0, abs(r.lado_direito)):
            return False
    return all(a.lower_limit - 1e-6 <= q <= a.limit + 1e-6 for a, q in zip(alimentos, x))


def _percentual(kg: dict[int, float]) -> dict[int, float]:
    total = sum(kg.values())
    return {i: 100 * v / total for i, v in kg.items()}


def comparar_caso(config: ConfigOtimizacao, grupo: str, nome: str, lote: Lote, alimentos: list[NASEMFeed],
                  sementes) -> tuple[dict, list[dict], dict]:
    """PL e RationOptimizer no mesmo problema. Devolve o resumo do caso, as execuções do SLSQP e as duas dietas."""
    config = replace(config, parametros=replace(config.parametros, perdas_energia="fixas"))
    inicio = time.perf_counter()
    pl = otimizar_lote(config, lote, alimentos, validar=False)
    segundos_pl = time.perf_counter() - inicio
    if not pl.viavel:
        raise ValueError(f"Sem dieta viável para o caso {nome}: {pl.status}")
    lote = lote.com(leite_kg=pl.leite_atendido_kg)  # se a PL reduziu o leite, o SLSQP resolve o mesmo leite
    x_pl = np.array([pl.kg_ms[a.rufas_id] for a in alimentos])
    folga_pl = min(validar_com_rufas(alimentos, x_pl, pl.exigencias, lote.peso_vivo, config.parametros).values())

    execucoes = []
    for semente in sementes:
        inicio = time.perf_counter()
        ro = otimizar_com_slsqp_rufas(config, lote, alimentos, semente)
        execucoes.append({"grupo": grupo, "caso": nome, "semente": semente, "custo": ro["custo_brl"],
                          "convergiu": ro["sucesso_scipy"], "menor_folga": ro["menor_folga"],
                          "restricoes_violadas": ", ".join(ro["restricoes_violadas"]), "mensagem": ro["mensagem"],
                          "segundos": time.perf_counter() - inicio, "kg_ms": ro["kg_ms"]})
    viaveis = [e for e in execucoes if e["menor_folga"] >= -TOLERANCIA_VIOLACAO]
    convergidas = [e for e in viaveis if e["convergiu"]]
    melhor = min(convergidas or viaveis or execucoes, key=lambda e: e["custo"])
    x_ro = np.array([melhor["kg_ms"].get(a.rufas_id, 0.0) for a in alimentos])
    pct_pl, pct_ro = _percentual(pl.kg_ms), _percentual(melhor["kg_ms"])

    resumo = {
        "grupo": grupo, "caso": nome, "custo_pl": pl.custo_brl, "cms_pl": pl.cms_kg, "menor_folga_pl": folga_pl,
        "pl_convergiu": pl.convergiu_slp, "pl_iteracoes": pl.iteracoes_slp,
        "custo_ro": melhor["custo"], "cms_ro": float(x_ro.sum()), "menor_folga_ro": melhor["menor_folga"],
        "ro_convergiu": melhor["convergiu"], "ro_viavel": melhor["menor_folga"] >= -TOLERANCIA_VIOLACAO,
        "n_convergidas": sum(e["convergiu"] for e in execucoes), "n_viaveis": len(viaveis),
        "n_execucoes": len(execucoes),
        "pl_vs_ro_pct": 100 * (pl.custo_brl - melhor["custo"]) / melhor["custo"],
        "diferenca_composicao_pct": 0.5 * sum(abs(pct_pl.get(i, 0.0) - pct_ro.get(i, 0.0))
                                              for i in set(pct_pl) | set(pct_ro)),
        "dieta_ro_atende_pl": dieta_atende_linhas_da_pl(config, lote, pl.resumo_exigencias, alimentos, x_ro),
        "segundos_pl": segundos_pl, "segundos_ro": float(np.mean([e["segundos"] for e in execucoes])),
    }
    return resumo, execucoes, {"pl": dict(pl.kg_ms), "ro": dict(melhor["kg_ms"])}


def rodar_comparacao(config: ConfigOtimizacao, n_sementes: int = 10, destino: Path | None = None) -> dict[str, object]:
    alimentos = carregar_alimentos(config)
    casos = [caso_da_aula(config)] + [("lote", l.nome, l, alimentos) for l in config.lotes] \
        + casos_pairwise(config, alimentos)
    resumos, execucoes, dietas = [], [], {}
    for grupo, nome, lote, alimentos_caso in casos:
        resumo, execucoes_caso, dietas[nome] = comparar_caso(config, grupo, nome, lote, alimentos_caso,
                                                             range(n_sementes))
        resumos.append(resumo)
        execucoes += execucoes_caso
    tabela = pd.DataFrame(resumos)
    if destino is not None:
        destino.mkdir(parents=True, exist_ok=True)
        tabela.to_csv(destino / "comparacao_ration_optimizer.csv", index=False)
        pd.DataFrame(execucoes).drop(columns="kg_ms").to_csv(
            destino / "comparacao_ration_optimizer_execucoes.csv", index=False)
    return {"tabela": tabela, "execucoes": execucoes, "dietas": dietas}
