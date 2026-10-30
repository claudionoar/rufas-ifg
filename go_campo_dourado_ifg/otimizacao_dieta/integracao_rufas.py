"""
Integração com o simulador RuFaS.

1. ``preparar_feed_json``: monta um ``feed.json`` a partir do ``feed_original.json`` da fazenda trocando só a ração do
   curral de lactação (``lac_cow``) e grava o preço de todos os alimentos usados com a tabela da configuração,
   convertida para US$/kg MS, que é a unidade do RuFaS. Com ``ration.user_input = true`` no animal.json, o RuFaS usa a
   receita (percentual da MS × CMS prevista pelo NASEM a cada reformulação) e minimiza o custo dentro de ±10% de cada
   ingrediente em todos os currais; por isso as duas rodadas de cada par usam a mesma tabela de preços.
2. ``preparar_cenario``: pasta ``fazenda/cenarios/<nome>/`` com esse feed.json, um farm_metadata.json que aponta para
   ele e os arquivos de tarefa do RuFaS, um por semente (``random_seeds``; sem essa chave o RuFaS sorteia a semente a
   cada rodada).
3. ``rodar_pares``: roda a dieta atual e a ótima com as mesmas sementes, algumas simulações ao mesmo tempo. Com a mesma
   semente e as mesmas entradas o RuFaS repete a simulação, então a diferença dentro de cada par vem da ração. Se uma
   simulação do par falha no RuFaS, a semente é descartada (com o erro registrado) e a próxima da lista entra.
4. ``gravar_extrato`` guarda as colunas da saída usadas na avaliação (``economia.avaliar_saida_rufas``) e em
   ``medir_lactacao``, para conferir os resultados sem os CSVs completos (centenas de MB cada).
"""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

from .config import ConfigOtimizacao
from .economia import COL_DIA, COL_LACTANTES, COL_LEITE, PREFIXO_FED, SUFIXO_FED, _cabecalho
from .modelo import ResultadoDieta

# O módulo fica em <repositório>/go_campo_dourado_ifg/otimizacao_dieta/.
RAIZ_REPOSITORIO = Path(__file__).resolve().parents[2]
LIMIAR_INCLUSAO_PCT = 0.01

ANIMAL = "AnimalModuleReporter."
COL_SEMENTE = "TaskManager.set_random_seed.random_seed (unitless)"
PREFIXO_RACAO = ANIMAL + "report_ration_per_animal.ration_per_animal_for_"
PREFIXO_METANO = ANIMAL + "report_enteric_methane_emission.enteric_methane_emission_for_"
PREFIXO_EXCRECAO = ANIMAL + "report_manure_excretions."
SUFIXO_N_URINA = "_urine_nitrogen (kg)"
SUFIXO_CMS = "dry_matter_intake_total (kg)"


def racao_media_rebanho(resultados: list[ResultadoDieta]) -> dict[int, float]:
    """Percentual da MS de cada ingrediente na ração única do curral de lactação (ponderada por vacas × CMS)."""
    total_ms = sum(r.lote.n_vacas * r.cms_kg for r in resultados if r.viavel)
    kg = {}
    for r in resultados:
        if not r.viavel:
            continue
        for i, q in r.kg_ms.items():
            kg[i] = kg.get(i, 0.0) + r.lote.n_vacas * q
    percentuais = {i: 100 * q / total_ms for i, q in kg.items() if 100 * q / total_ms >= LIMIAR_INCLUSAO_PCT}
    return _arredondar_para_100(percentuais)


def _arredondar_para_100(percentuais: dict[int, float], casas: int = 2) -> dict[int, float]:
    soma = sum(percentuais.values())
    arredondados = {i: round(100 * v / soma, casas) for i, v in percentuais.items()}
    maior = max(arredondados, key=arredondados.get)
    arredondados[maior] = round(arredondados[maior] + 100 - sum(arredondados.values()), casas)
    return arredondados


def _ler_json(caminho: Path) -> dict:
    with open(caminho, encoding="utf-8") as arquivo:
        return json.load(arquivo)


def _gravar_json(caminho: Path, dados: dict) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with open(caminho, "w", encoding="utf-8", newline="\n") as arquivo:
        json.dump(dados, arquivo, indent=2, ensure_ascii=False)
        arquivo.write("\n")


def _relativo(caminho: Path) -> str:
    """Caminho a partir da raiz do repositório, que é como o RuFaS lê os arquivos de entrada."""
    return Path(caminho).resolve().relative_to(RAIZ_REPOSITORIO).as_posix()


def caminho_feed_json(config: ConfigOtimizacao) -> Path:
    return config.dir_fazenda / "inputs_resolvidos" / "feed.json"


def caminho_task_manager(config: ConfigOtimizacao) -> Path:
    return config.dir_fazenda / "pipeline_task_manager_metadata.json"


def _feed_original(config: ConfigOtimizacao) -> dict:
    """``feed.json`` da fazenda antes da primeira gravação do módulo (copiado para ``feed_original.json``)."""
    atual = caminho_feed_json(config)
    original = atual.with_name("feed_original.json")
    if not original.exists():
        shutil.copy2(atual, original)
    return _ler_json(original)


def montar_feed(config: ConfigOtimizacao, racao_lactacao: dict[int, float]) -> dict:
    """``feed.json`` original com a ração ``lac_cow`` trocada e todos os preços da tabela da configuração, em US$."""
    original = _feed_original(config)
    feed = copy.deepcopy(original)
    for racao in feed["rations"]:
        if racao["animal_combination"] == "lac_cow":
            racao["feeds"] = [{"feed_type": i, "ration_percentage": p} for i, p in sorted(racao_lactacao.items())]

    usados = sorted({f["feed_type"] for r in feed["rations"] for f in r["feeds"]})
    gabarito = {f["feed_type"]: f for f in original["feeds"]}
    exemplo = original["feeds"][0]
    feed["feeds"] = []
    for i in usados:
        entrada = copy.deepcopy(gabarito.get(i, exemplo))
        entrada["feed_type"] = i
        entrada["purchased_feed_cost"] = round(config.preco(i) / config.cambio_brl_por_usd, 4)
        feed["feeds"].append(entrada)
    return feed


def preparar_feed_json(config: ConfigOtimizacao, racao_lactacao: dict[int, float], destino: Path | None = None
                       ) -> Path:
    """Grava o ``feed.json`` (por padrão, o da fazenda) com a ração de lactação dada."""
    destino = destino or caminho_feed_json(config)
    _gravar_json(destino, montar_feed(config, racao_lactacao))
    return destino


def preparar_cenario(config: ConfigOtimizacao, nome: str, racao_lactacao: dict[int, float], sementes: list[int]
                     ) -> dict[int, tuple[Path, str]]:
    """
    Entradas do RuFaS para um cenário em ``fazenda/cenarios/<nome>/``: feed.json, farm_metadata.json que aponta para
    ele (as demais entradas são as da fazenda) e, por semente, o arquivo de tarefas e o task manager.

    Devolve, por semente, o task manager e o prefixo dos arquivos de saída.
    """
    pasta = config.dir_fazenda / "cenarios" / nome
    feed = preparar_feed_json(config, racao_lactacao, pasta / "feed.json")
    metadata = _ler_json(config.dir_fazenda / "farm_metadata.json")
    metadata["files"]["feed"]["path"] = _relativo(feed)
    _gravar_json(pasta / "farm_metadata.json", metadata)

    tarefas_fazenda = _ler_json(config.dir_fazenda / "pipeline_tasks.json")
    gerente_fazenda = _ler_json(caminho_task_manager(config))
    arquivos = {}
    for semente in sementes:
        prefixo = f"{config.fazenda}_{nome}_s{semente}"
        tarefas = copy.deepcopy(tarefas_fazenda)
        for tarefa in tarefas["tasks"]:
            tarefa.pop("random_seed", None)
            tarefa.update(metadata_file_path=_relativo(pasta / "farm_metadata.json"), output_prefix=prefixo,
                          random_seeds=[semente])
        _gravar_json(pasta / f"tarefas_s{semente}.json", tarefas)
        gerente = copy.deepcopy(gerente_fazenda)
        gerente["files"]["tasks"].update(title=f"Pipeline task — {prefixo}",
                                         path=_relativo(pasta / f"tarefas_s{semente}.json"))
        _gravar_json(pasta / f"task_manager_s{semente}.json", gerente)
        arquivos[semente] = (pasta / f"task_manager_s{semente}.json", prefixo)
    return arquivos


def csv_mais_recente(prefixo: str, desde: float = 0.0) -> Path | None:
    candidatos = [c for c in (RAIZ_REPOSITORIO / "output" / "CSVs").glob(f"{prefixo}_saved_variables_*.csv")
                  if c.stat().st_mtime >= desde]
    return max(candidatos, key=lambda c: c.stat().st_mtime) if candidatos else None


def rodar_rufas(task_manager: Path, prefixo: str, sem_graficos: bool = True) -> tuple[int, Path | None, str]:
    """Executa uma simulação completa. Retorna (código de saída, CSV gerado, últimas linhas do log)."""
    comando = [sys.executable, "main.py", "-p", _relativo(task_manager)]
    if sem_graficos:
        comando.append("-g")
    inicio = time.time()
    processo = subprocess.run(comando, cwd=RAIZ_REPOSITORIO, capture_output=True, text=True)
    log = (processo.stdout + processo.stderr).strip().splitlines()
    return processo.returncode, csv_mais_recente(prefixo, inicio), "\n".join(log[-15:])


def colunas_extrato(colunas: list[str]) -> list[str]:
    return [c for c in colunas
            if c in (COL_DIA, COL_LEITE, COL_LACTANTES, COL_SEMENTE)
            or (c.startswith(PREFIXO_FED) and c.endswith(SUFIXO_FED))
            or c.startswith(PREFIXO_RACAO) or c.startswith(PREFIXO_METANO)
            or (c.startswith(PREFIXO_EXCRECAO) and c.endswith(SUFIXO_N_URINA))]


def gravar_extrato(csv: Path, destino: Path) -> Path:
    """Colunas usadas, sem as linhas vazias (as séries diárias continuam alinhadas a partir da primeira linha)."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    dados = pd.read_csv(csv, usecols=colunas_extrato(_cabecalho(csv)))
    dados.dropna(how="all").to_csv(destino, index=False)
    return destino


def _rodar_cenario(nome: str, semente: int, gerente: Path, prefixo: str, pasta_extratos: Path) -> dict:
    inicio = time.perf_counter()
    codigo, csv, log = rodar_rufas(gerente, prefixo)
    segundos = time.perf_counter() - inicio
    rodada = {"cenario": nome, "semente": semente, "prefixo": prefixo, "segundos": segundos}
    if codigo != 0 or csv is None:
        # O RuFaS pode terminar com código 0 sem gravar o CSV; a causa fica na linha do erro do log.
        erro = next((l.strip() for l in reversed(log.splitlines()) if "Error:" in l), log.splitlines()[-1] if log else "")
        print(f"    {prefixo}: falhou ({segundos:.0f} s): {erro}", flush=True)
        return rodada | {"erro": erro}
    print(f"    {prefixo}: ok ({segundos:.0f} s)", flush=True)
    return rodada | {"csv": csv, "extrato": gravar_extrato(csv, pasta_extratos / f"{prefixo}.csv")}


def rodar_pares(arquivos: dict[str, dict[int, tuple[Path, str]]], sementes: list[int], n_pares: int,
                paralelas: int, pasta_extratos: Path) -> tuple[list[dict], list[dict]]:
    """
    Roda os cenários de cada semente, na ordem da lista, até completar ``n_pares`` pares (os cenários de um par rodam
    juntos quando ``paralelas`` ≥ 2) e grava os extratos.

    O RuFaS 1.0.5 às vezes para no meio da simulação (por exemplo, no separador do módulo de esterco). Uma semente em
    que algum cenário falha é registrada em ``descartadas``, com o erro, e a próxima semente da lista entra no lugar.
    Devolve (rodadas dos pares completos, sementes descartadas).
    """
    rodadas, descartadas = [], []
    with ThreadPoolExecutor(max_workers=max(1, paralelas)) as executor:
        for semente in sementes:
            if len(rodadas) >= n_pares * len(arquivos):
                break
            futuros = [executor.submit(_rodar_cenario, nome, semente, *arquivos[nome][semente], pasta_extratos)
                       for nome in arquivos]
            par = [f.result() for f in futuros]
            erros = {r["cenario"]: r["erro"] for r in par if "erro" in r}
            if erros:
                descartadas.append({"semente": semente, "erros": erros})
            else:
                rodadas.extend(par)
    return rodadas, descartadas


def medir_lactacao(csv: Path) -> dict[str, object]:
    """
    Ração realmente fornecida no curral de lactação (% da MS em cada reformulação), CMS, metano e N urinário.

    Metano e N urinário são os do curral de lactação divididos pelo número de vacas em lactação do rebanho.
    """
    colunas = _cabecalho(csv)
    curral = next(c[len(PREFIXO_RACAO):].split(".")[0] for c in colunas if c.startswith(PREFIXO_RACAO + "LAC_COW"))
    prefixo = f"{PREFIXO_RACAO}{curral}."
    racao = [c for c in colunas if c.startswith(prefixo) and not c.endswith(SUFIXO_CMS)]
    total = prefixo + SUFIXO_CMS
    metano = f"{PREFIXO_METANO}{curral} (g)"
    n_urina = f"{PREFIXO_EXCRECAO}{curral}{SUFIXO_N_URINA}"
    dados = pd.read_csv(csv, usecols=[COL_LACTANTES, total, metano, n_urina, *racao])
    p = dados[racao + [total]].dropna()
    pct = p[racao].div(p[total], axis=0) * 100
    pct.columns = [c[len(prefixo):].split(" ")[0] for c in racao]
    return {
        "curral": curral, "reformulacoes": int(len(p)), "cms_media_kg": float(p[total].mean()),
        "racao_pct_media": pct.mean().round(3).to_dict(), "racao_pct_min": pct.min().round(3).to_dict(),
        "racao_pct_max": pct.max().round(3).to_dict(),
        "metano_g_vaca": float((dados[metano] / dados[COL_LACTANTES]).mean()),
        "n_urina_g_vaca": float((dados[n_urina] * 1000 / dados[COL_LACTANTES]).mean()),
    }
