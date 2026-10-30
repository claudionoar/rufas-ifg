"""
Linha de comando do módulo de otimização de dieta.

    python -m otimizacao_dieta otimizar      [--config CAMINHO]
    python -m otimizacao_dieta sensibilidade [--n-base 256] [--processos 4]
    python -m otimizacao_dieta pairwise      [--fatores preco_167,leite_kg,...]
    python -m otimizacao_dieta comparar      [--sementes 10]
    python -m otimizacao_dieta rufas         [--sem-rodar]
    python -m otimizacao_dieta relatorio
    python -m otimizacao_dieta tudo          [--sem-rufas]

Cada etapa grava em resultados/<etapa>/ (ao lado do config_otimizacao.json); o relatório e as figuras ficam em
resultados/.

Rodar da raiz do repositório (é de lá que o código completo do RuFaS é importado), com go_campo_dourado_ifg no
PYTHONPATH. O serviço "otimizador" do docker-compose já faz as duas coisas.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

from .biblioteca import carregar_alimentos
from .comparacao import rodar_comparacao
from .config import ConfigOtimizacao, carregar_config
from .economia import (avaliar_saida_rufas, custo_marginal_leite, economia_rebanho, indicadores_dieta_atual,
                       indicadores_otimos, resumo_rebanho)
from .exigencias import sem_gestacao_para_nao_prenhe
from .integracao_rufas import (medir_lactacao, preparar_cenario, preparar_feed_json, racao_media_rebanho,
                               rodar_pares)
from .modelo import otimizar_lote
from .pairwise import rodar_pairwise
from .relatorio import gerar_relatorio
from .sensibilidade import fatores_da_config, fatores_mais_influentes, rodar_sobol
from .simplex import resolver_tabular

CONFIG_PADRAO = Path("go_campo_dourado_ifg/config_otimizacao.json")
# Indicadores da simulação comparados dentro de cada par (dieta ótima − dieta atual, mesma semente).
INDICADORES_PAR = ["leite_total_kg", "leite_kg_vaca_lactante_dia", "vacas_lactantes_media",
                   "custo_alimentacao_total_brl", "receita_leite_total_brl", "iofc_total_brl", "iofc_brl_ano",
                   "custo_alimentar_brl_kg_leite"]


def _gravar_json(caminho: Path, dados: object) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with open(caminho, "w", encoding="utf-8", newline="\n") as arquivo:
        json.dump(dados, arquivo, indent=2, ensure_ascii=False, default=float)
        arquivo.write("\n")


def etapa_otimizar(config: ConfigOtimizacao) -> dict:
    pasta = config.dir_etapa("otimizar")
    pasta.mkdir(parents=True, exist_ok=True)
    alimentos = carregar_alimentos(config)
    nomes = {i.rufas_id: i.nome for i in config.ingredientes}
    lotes, economia, otimos, atuais, marginais, inviaveis_atual = {}, [], [], [], [], {}
    resultados = []
    for lote in config.lotes:
        r = otimizar_lote(config, lote, alimentos, nomes)
        resultados.append(r)
        if not r.viavel:
            raise SystemExit(f"Sem dieta viável para o lote {lote.id}: {r.status}")
        lotes[lote.id] = {
            "nome": lote.nome, "n_vacas": lote.n_vacas, "custo_brl": r.custo_brl, "cms_kg": r.cms_kg,
            "kg_ms": {str(i): v for i, v in r.kg_ms.items()},
            "percentual_ms": {str(i): v for i, v in r.percentual_ms().items()},
            "exigencias": r.resumo_exigencias, "leite_atendido_kg": r.leite_atendido_kg,
            "reducao_leite_kg": r.reducao_leite_kg, "iteracoes_slp": r.iteracoes_slp,
            "convergiu_slp": r.convergiu_slp,
            "restricoes": [l.__dict__ | {"ativa": l.ativa} for l in r.restricoes],
            "custo_reduzido": {str(i): v for i, v in r.custo_reduzido.items()},
            "validacao_rufas": r.validacao_rufas, "validacao_ok": r.validacao_ok,
        }
        ind = indicadores_otimos(config, r)
        otimos.append(ind)
        economia.append(ind.como_dict() | {"lote_id": lote.id, "lote": lote.nome, "cenario": "ótima",
                                            "atende_rufas": "sim" if r.validacao_ok else "não"})
        ind_atual, aval = indicadores_dieta_atual(config, lote, alimentos)
        atuais.append(ind_atual)
        falhas = [k for k, v in aval["validacao_rufas"].items() if v < -1e-3 * max(1.0, abs(v))]
        if falhas:
            inviaveis_atual[lote.id] = falhas
        economia.append(ind_atual.como_dict() | {"lote_id": lote.id, "lote": lote.nome, "cenario": "atual (GO)",
                                                  "atende_rufas": "sim" if not falhas else "não: " + ", ".join(falhas)})
        marginais.append({"lote_id": lote.id, "lote": lote.nome} | custo_marginal_leite(config, lote, alimentos, r))
        print(f"  {lote.nome:28s} custo {r.custo_brl:7.3f} R$/vaca/dia  CMS {r.cms_kg:5.2f} kg  "
              f"IOFC {ind.iofc:7.3f}  validação RuFaS {'ok' if r.validacao_ok else 'FALHOU'}")

    pd.DataFrame(economia).to_csv(pasta / "economia_lotes.csv", index=False)
    pd.DataFrame(marginais).to_csv(pasta / "custo_marginal_leite.csv", index=False)
    _gravar_json(pasta / "economia_rebanho.json", {"otima": resumo_rebanho(otimos), "atual": resumo_rebanho(atuais),
                                                    "atual_inviavel": inviaveis_atual})

    # A mesma comparação com uma hipótese trocada de cada vez.
    variantes = {"rufas_1_0_5": economia_rebanho(config, alimentos)}
    with sem_gestacao_para_nao_prenhe():
        variantes["sem_erro_gestacao"] = economia_rebanho(config, alimentos)
    variantes["perdas_fixas"] = economia_rebanho(
        replace(config, parametros=replace(config.parametros, perdas_energia="fixas")), alimentos)
    variantes["cms_fixa"] = economia_rebanho(
        replace(config, parametros=replace(config.parametros, tolerancia_cms=0.001)), alimentos)
    _gravar_json(pasta / "economia_variantes.json", variantes)
    for nome, v in variantes.items():
        print(f"  variante {nome:18s} custo da dieta {-v['reducao_custo_pct']:+6.2f}%  "
              f"IOFC {v['diferenca_iofc_brl_ano']:+12,.0f} R$/ano  atual inviável em {list(v['atual_inviavel'])}")

    lote_ref = config.lote(config.sensibilidade.get("lote", config.lotes[-1].id))
    r_ref = next(r for r in resultados if r.lote.id == lote_ref.id)
    quadro = resolver_tabular(r_ref.problema)
    quadro.como_dataframe().to_csv(pasta / "quadro_final_simplex.csv", index=False)
    quadro.faixa_lados_direitos.to_csv(pasta / "simplex_faixas_restricoes.csv", index=False)
    quadro.faixa_custos.to_csv(pasta / "simplex_faixas_precos.csv", index=False)
    rotulos = {str(i.rufas_id): i.nome for i in config.ingredientes} | {"m": "proteína microbiana (variável auxiliar)"}
    base = []
    for linha, j in enumerate(quadro.base):
        nome = quadro.nomes_colunas[j]
        rotulo = rotulos.get(nome, nome)
        for prefixo, texto in [("folga_max_", "folga do limite máx. de "), ("folga_", "folga de "),
                               ("excesso_", "excesso sobre "), ("art_", "artificial de ")]:
            if nome.startswith(prefixo):
                alvo = nome.removeprefix(prefixo)
                rotulo = texto + rotulos.get(alvo, alvo)
                break
        valor = float(quadro.tableau[linha, -1])
        if j < len(r_ref.problema.limite_inferior):
            valor += float(r_ref.problema.limite_inferior[j])
        base.append({"variavel": nome, "variavel_rotulo": rotulo, "valor": valor})
    _gravar_json(pasta / "simplex_base.json", base)

    dados = {
        "nomes": {str(k): v for k, v in nomes.items()},
        "lotes": lotes,
        "simplex": {"lote": lote_ref.id, "objetivo": quadro.valor_objetivo, "objetivo_highs": r_ref.custo_brl},
        "racao_media_lactacao_pct": {str(k): v for k, v in racao_media_rebanho(resultados).items()},
    }
    _gravar_json(pasta / "dietas.json", dados)
    print(f"  Simplex tabular: {quadro.valor_objetivo:.6f}  |  HiGHS: {r_ref.custo_brl:.6f}  "
          "(comparação com o RationOptimizer do RuFaS: etapa 'comparar')")
    return dados


def etapa_sensibilidade(config: ConfigOtimizacao, n_base: int | None, processos: int | None) -> None:
    inicio = time.time()
    pasta = config.dir_etapa("sensibilidade")
    saida = rodar_sobol(config, n_base=n_base, processos=processos, destino=pasta)
    controle = saida["controle"]
    _gravar_json(pasta / "sobol_resumo.json",
                 {"n_avaliacoes": saida["n_avaliacoes"], "n_base": saida["n_base"], "k": len(saida["fatores"]),
                  "invalidas": saida["invalidas"], **controle, "segundos": time.time() - inicio})
    top = saida["indices"][saida["indices"].saida == "iofc_brl"].sort_values("ST", ascending=False).head(5)
    print(f"  {saida['n_avaliacoes']} PLs em {time.time() - inicio:.0f} s; sem solução: {saida['invalidas']}; "
          f"SLP sem convergência: {controle['nao_convergiram']}; dieta fora do RuFaS: {controle['nao_atendem_rufas']}"
          f" (pior folga {controle['pior_folga_pct_exigencia']:.2f}% da exigência). Mais influentes no IOFC: "
          + ", ".join(f"{r.rotulo} (ST={r.ST:.2f})" for r in top.itertuples()))


def etapa_pairwise(config: ConfigOtimizacao, fatores: list[str] | None) -> None:
    if not fatores:
        indices_arq = config.dir_etapa("sensibilidade") / "sobol_indices.csv"
        k = int(config.pairwise.get("k_auto", 6))
        if config.pairwise.get("fatores", "auto") != "auto":
            fatores = list(config.pairwise["fatores"])
        elif indices_arq.exists():
            fatores = fatores_mais_influentes(pd.read_csv(indices_arq), k)
        else:
            fatores = [f.nome for f in fatores_da_config(config)][:k]
            print("  (sem índices de Sobol; usando os primeiros fatores da configuração)")
    pasta = config.dir_etapa("pairwise")
    saida = rodar_pairwise(config, fatores, destino=pasta)
    rotulos = {f.nome: f.rotulo for f in saida["fatores"]}
    tabela = saida["tabela"]
    _gravar_json(pasta / "pairwise_resumo.json",
                 {"fatores": fatores, "rotulos": [rotulos[f] for f in fatores],
                  "n_cenarios": len(tabela), "fatorial_completo": saida["fatorial_completo"],
                  "pares_descobertos": len(saida["pares_descobertos"]),
                  "nao_convergiram": int((~tabela.convergiu_slp.astype(bool)).sum()),
                  "nao_atendem_rufas": int((~tabela.atende_rufas.astype(bool)).sum()),
                  "max_iteracoes_slp": int(tabela.iteracoes_slp.max())})
    print(f"  {len(tabela)} cenários (fatorial completo: {saida['fatorial_completo']}); "
          f"pares descobertos: {len(saida['pares_descobertos'])}; dieta fora do RuFaS: "
          f"{int((~tabela.atende_rufas.astype(bool)).sum())}")


def etapa_comparar(config: ConfigOtimizacao, n_sementes: int) -> None:
    pasta = config.dir_etapa("comparar")
    saida = rodar_comparacao(config, n_sementes, destino=pasta)
    tabela = saida["tabela"]
    nomes = {str(i.rufas_id): i.nome for i in config.ingredientes}
    _gravar_json(pasta / "comparacao_ration_optimizer.json", {
        "n_sementes": n_sementes, "perdas_energia": "fixas", "nomes": nomes,
        "resumo": tabela.to_dict("records"),
        "dietas": {caso: {lado: {str(i): v for i, v in kg.items()} for lado, kg in d.items()}
                   for caso, d in saida["dietas"].items()},
    })
    for linha in tabela.itertuples():
        print(f"  {linha.caso:26s} PL {linha.custo_pl:9.4f}  RationOptimizer {linha.custo_ro:9.4f} "
              f"(convergiu {linha.n_convergidas}/{linha.n_execucoes}, viável {linha.n_viaveis}/{linha.n_execucoes})  "
              f"PL − RO {linha.pl_vs_ro_pct:+6.2f}%")


def _resumir_diferencas(valores: list[float]) -> dict[str, float]:
    """Média, desvio, extremos e intervalo de 95% (t de Student) das diferenças entre os pares."""
    v = np.asarray(valores, dtype=float)
    n = len(v)
    media = float(v.mean())
    desvio = float(v.std(ddof=1)) if n > 1 else float("nan")
    meia = float(stats.t.ppf(0.975, n - 1) * desvio / np.sqrt(n)) if n > 1 else float("nan")
    return {"n": n, "media": media, "desvio": desvio, "min": float(v.min()), "max": float(v.max()),
            "ic95_inf": media - meia, "ic95_sup": media + meia}


def etapa_rufas(config: ConfigOtimizacao, rodar: bool) -> None:
    with open(config.dir_etapa("otimizar") / "dietas.json", encoding="utf-8") as arquivo:
        racao = {int(k): v for k, v in json.load(arquivo)["racao_media_lactacao_pct"].items()}
    print(f"  feed.json da fazenda com a ração ótima: {preparar_feed_json(config, racao)}")
    candidatas = [int(s) for s in config.rufas.get("sementes", [44])]
    n_pares = int(config.rufas.get("pares", len(candidatas)))
    cenarios = {"atual": config.dieta_atual, "otima": racao}
    arquivos = {nome: preparar_cenario(config, nome, r, candidatas) for nome, r in cenarios.items()}
    print(f"  cenários {', '.join(cenarios)} × sementes candidatas {candidatas}: {config.dir_fazenda / 'cenarios'}")
    if not rodar:
        return
    pasta = config.dir_etapa("rufas")
    paralelas = int(config.rufas.get("simulacoes_paralelas", 1))
    print(f"  Rodando {n_pares} pares de simulações, {paralelas} por vez (dezenas de minutos)...", flush=True)
    lista_rodadas, descartadas = rodar_pares(arquivos, candidatas, n_pares, paralelas, pasta / "extratos")
    rodadas = {(r["cenario"], r["semente"]): r for r in lista_rodadas}
    sementes = [s for s in candidatas if ("atual", s) in rodadas]
    if not sementes:
        raise SystemExit(f"Nenhum par de simulações terminou: {descartadas}")
    if len(sementes) < n_pares:
        print(f"  Atenção: só {len(sementes)} de {n_pares} pares terminaram (sementes candidatas esgotadas).")

    linhas, pares = [], []
    for semente in sementes:
        avaliacao = {}
        for nome in cenarios:
            r = rodadas[(nome, semente)]
            avaliacao[nome] = avaliar_saida_rufas(r["extrato"], config) | {
                "csv": str(r["csv"]), "extrato": r["extrato"].relative_to(pasta).as_posix(),
                "segundos": r["segundos"], "lactacao": medir_lactacao(r["extrato"])}
            linhas.append({"cenario": nome, "semente": semente, "csv": Path(r["csv"]).name,
                           **{k: avaliacao[nome][k] for k in ["dias_simulados", *INDICADORES_PAR]},
                           "cms_lactacao_kg": avaliacao[nome]["lactacao"]["cms_media_kg"],
                           "segundos": r["segundos"]})
        pares.append({"semente": semente, "base": avaliacao["atual"], "ifg": avaliacao["otima"],
                      "diferenca": {k: avaliacao["otima"][k] - avaliacao["atual"][k] for k in INDICADORES_PAR}})
    resumo = {k: _resumir_diferencas([p["diferenca"][k] for p in pares]) for k in INDICADORES_PAR}
    pd.DataFrame(linhas).to_csv(pasta / "rufas_rodadas.csv", index=False)
    # "base" e "ifg" repetem o primeiro par, para quem lia a versão de uma rodada só.
    _gravar_json(pasta / "rufas_comparacao.json", {
        "racao_lactacao": {str(k): v for k, v in racao.items()},
        "racao_atual": {str(k): v for k, v in config.dieta_atual.items()},
        "sementes": sementes, "sementes_candidatas": candidatas, "sementes_descartadas": descartadas,
        "pares": pares, "resumo": resumo, "base": pares[0]["base"], "ifg": pares[0]["ifg"]})
    for d in descartadas:
        print(f"  semente {d['semente']} descartada: " + "; ".join(f"{c}: {e}" for c, e in d["erros"].items()))
    for p in pares:
        print(f"  semente {p['semente']}: IOFC/ano atual R$ {p['base']['iofc_brl_ano']:,.0f}  ótima "
              f"R$ {p['ifg']['iofc_brl_ano']:,.0f}  diferença R$ {p['diferenca']['iofc_brl_ano']:+,.0f}")
    d = resumo["iofc_brl_ano"]
    print(f"  diferença média R$ {d['media']:+,.0f}/ano (IC 95%: R$ {d['ic95_inf']:,.0f} a R$ {d['ic95_sup']:,.0f})")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m otimizacao_dieta", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("etapa", choices=["otimizar", "sensibilidade", "pairwise", "comparar", "rufas", "relatorio",
                                          "tudo"])
    parser.add_argument("--config", default=str(CONFIG_PADRAO))
    parser.add_argument("--n-base", type=int, default=None, help="Tamanho base da amostra de Sobol (potência de 2).")
    parser.add_argument("--processos", type=int, default=None)
    parser.add_argument("--fatores", default=None, help="Fatores do pairwise separados por vírgula.")
    parser.add_argument("--sementes", type=int, default=10,
                        help="Execuções do RationOptimizer por caso na comparação (ele sorteia o ponto inicial).")
    parser.add_argument("--sem-rodar", action="store_true",
                        help="Só grava o feed.json e os cenários pareados, sem rodar o RuFaS.")
    parser.add_argument("--sem-rufas", action="store_true", help="Na etapa 'tudo', pula a simulação do RuFaS.")
    args = parser.parse_args(argv)

    config = carregar_config(args.config)
    config.dir_resultados.mkdir(parents=True, exist_ok=True)
    np.seterr(all="ignore")
    etapas = (["otimizar", "sensibilidade", "pairwise", "comparar", "rufas", "relatorio"] if args.etapa == "tudo"
              else [args.etapa])
    if args.etapa == "tudo" and args.sem_rufas:
        etapas.remove("rufas")
    for etapa in etapas:
        print(f"[{etapa}]")
        if etapa == "otimizar":
            etapa_otimizar(config)
        elif etapa == "sensibilidade":
            etapa_sensibilidade(config, args.n_base, args.processos)
        elif etapa == "pairwise":
            etapa_pairwise(config, args.fatores.split(",") if args.fatores else None)
        elif etapa == "comparar":
            etapa_comparar(config, args.sementes)
        elif etapa == "rufas":
            etapa_rufas(config, not args.sem_rodar)
        elif etapa == "relatorio":
            print(f"  {gerar_relatorio(config)}")


if __name__ == "__main__":
    main(sys.argv[1:])
