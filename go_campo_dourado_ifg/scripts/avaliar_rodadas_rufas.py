"""Reavalia todas as rodadas de simulação do RuFaS guardadas em output/CSVs com a tabela de preços atual.

Serve para o histórico: as rodadas antigas não tinham semente fixa (o arquivo de tarefas usava ``random_seed``, que o
RuFaS 1.0.5 ignora, e sem ``random_seeds`` ele sorteia a semente), então não se repetiam. A comparação dieta atual ×
dieta ótima com as mesmas sementes é a da etapa rufas (resultados/rufas/rufas_comparacao.json). A ração de lactação
de cada rodada é lida do próprio CSV (RationManager.set_user_defined_rations), para separar as rodadas que usaram a
ração ótima atual.

Uso (pela pasta docker/):
    docker compose run --rm --entrypoint python otimizador go_campo_dourado_ifg/scripts/avaliar_rodadas_rufas.py

Saída: go_campo_dourado_ifg/resultados/rufas/rodadas_rufas.csv
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

FAZ = Path(__file__).resolve().parents[1]
RAIZ = FAZ.parent
# Rodado como script, o Python não põe a raiz no caminho; é de lá que vem o código completo do RuFaS.
sys.path[:0] = [str(RAIZ), str(FAZ)]

from otimizacao_dieta.config import carregar_config  # noqa: E402
from otimizacao_dieta.economia import avaliar_saida_rufas  # noqa: E402

CSVS = RAIZ / "output" / "CSVs"
RES = FAZ / "resultados"
PREFIXOS = {"GO_campo_dourado_saved_variables": "base", "GO_campo_dourado_claudio_saved_variables": "ifg",
            "go_campo_dourado_ifg_saved_variables": "ifg"}
# Rodadas pareadas da etapa rufas: <fazenda>_<cenário>_s<semente>_saved_variables...
PAREADA = re.compile(r"^go_campo_dourado_ifg_(atual|otima)_s(\d+)_saved_variables")
COLUNA_RACAO = "RationManager.set_user_defined_rations.user_defined_ration."
CALCARIO = 234


def valores_racao(csv: Path) -> dict[int, set[float]]:
    """Percentuais de cada alimento nas rações definidas pelo usuário (todas as categorias de animal)."""
    colunas = [c for c in pd.read_csv(csv, nrows=0).columns if c.startswith(COLUNA_RACAO)]
    dados = pd.read_csv(csv, usecols=colunas, nrows=2000)
    return {int(c[len(COLUNA_RACAO):].split(" ")[0]): set(dados[c].dropna().round(2)) for c in colunas}


def contem(valores: dict[int, set[float]], racao: dict[int, float]) -> bool:
    return all(any(abs(v - p) < 0.006 for v in valores.get(i, ())) for i, p in racao.items())


def descrever(valores: dict[int, set[float]], otima: dict[int, float], original: dict[int, float]) -> str:
    if contem(valores, otima):
        return "ótima atual"
    if contem(valores, original):
        return "original da fazenda"
    return "ótima anterior (sem calcário)" if not valores.get(CALCARIO) else "outra"


def main() -> None:
    config = carregar_config(FAZ / "config_otimizacao.json")
    dietas = json.loads((RES / "otimizar" / "dietas.json").read_text(encoding="utf-8"))
    otima = {int(k): v for k, v in dietas["racao_media_lactacao_pct"].items()}
    comparacao = json.loads((RES / "rufas" / "rufas_comparacao.json").read_text(encoding="utf-8"))
    desta = {Path(par[lado]["csv"]).name for par in comparacao.get("pares", [comparacao]) for lado in ("base", "ifg")}
    linhas = []
    for csv in sorted(CSVS.glob("*_saved_variables_csv_all_variables.txt_*.csv")):
        pareada = PAREADA.match(csv.name)
        fazenda = (f"ifg {pareada[1]} s{pareada[2]}" if pareada
                   else next((f for p, f in PREFIXOS.items() if csv.name.startswith(p + "_")), None))
        if fazenda is None:
            continue
        carimbo = csv.stem.rsplit(".txt_", 1)[1]  # 28-Oct-2026_Wed_12-02-07
        dia, _, hora = carimbo.split("_")
        racao = descrever(valores_racao(csv), otima, config.dieta_atual)
        resultado = avaliar_saida_rufas(csv, config)
        linhas.append({"arquivo": csv.name, "fazenda": fazenda,
                       "data_hora": datetime.strptime(f"{dia} {hora}", "%d-%b-%Y %H-%M-%S").isoformat(),
                       "racao_descricao": racao, "racao_igual_atual": racao == "ótima atual",
                       "esta_execucao": csv.name in desta,
                       **{k: resultado[k] for k in ("dias_simulados", "leite_total_kg", "custo_alimentacao_total_brl",
                                                    "receita_leite_total_brl", "iofc_brl_ano",
                                                    "custo_alimentar_brl_kg_leite")}})
        print(f"  {fazenda:14s} {carimbo}  {racao:30s} IOFC/ano R$ {resultado['iofc_brl_ano']:,.0f}", flush=True)
    destino = RES / "rufas" / "rodadas_rufas.csv"
    destino.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(linhas).to_csv(destino, index=False)
    print(destino)


if __name__ == "__main__":
    main()
