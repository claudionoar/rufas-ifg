"""Gráficos e relatório em Markdown a partir dos arquivos que as etapas gravam em ``resultados/<etapa>``."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from .biblioteca import caminho_biblioteca, ler_tabela  # noqa: E402
from .config import ConfigOtimizacao  # noqa: E402

ROTULO_NUTRIENTE = {
    "ell": ("Mcal de ELl", 1.0, "Mcal"),
    "pm_min": ("100 g de proteína metabolizável", 100.0, "100 g"),
    "fosforo": ("g de fósforo absorvível", 1.0, "g"),
    "calcio": ("g de cálcio absorvível", 1.0, "g"),
    "cms_min": ("kg de CMS mínima", 1.0, "kg"),
    "cms_max": ("kg de CMS máxima", 1.0, "kg"),
}


def _brl(valor: float, casas: int = 2) -> str:
    if valor is None or (isinstance(valor, float) and np.isnan(valor)):
        return "—"
    texto = f"{valor:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {texto}"


def _num(valor: float, casas: int = 2) -> str:
    if valor is None or (isinstance(valor, float) and (np.isnan(valor) or np.isinf(valor))):
        return "—" if not (isinstance(valor, float) and np.isinf(valor)) else ("+∞" if valor > 0 else "−∞")
    return f"{valor:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _tabela_md(df: pd.DataFrame) -> str:
    cabecalho = "| " + " | ".join(map(str, df.columns)) + " |"
    separador = "|" + "|".join("---" for _ in df.columns) + "|"
    linhas = ["| " + " | ".join(map(str, linha)) + " |" for linha in df.itertuples(index=False)]
    return "\n".join([cabecalho, separador, *linhas])


def dieta_materia_natural(kg_ms: dict[int, float], ms_pct: dict[int, float],
                          precos_kg_ms: dict[int, float]) -> pd.DataFrame:
    """
    Dieta em matéria natural (o que vai no cocho) e preços por tonelada, como no quadro da aula.

    A PL trabalha em matéria seca porque exigências e composição dos alimentos estão em base seca; aqui a dieta volta
    para a base em que o produtor pesa e compra: kg MN = kg MS / MS e R$/t MN = R$/kg MS × MS × 1000.
    """
    linhas = []
    for i, kg in kg_ms.items():
        if kg <= 1e-4:
            continue
        fracao_ms = ms_pct[i] / 100
        linhas.append({"rufas_id": i, "ms_pct": ms_pct[i], "kg_ms": kg, "kg_mn": kg / fracao_ms,
                       "brl_t_ms": precos_kg_ms[i] * 1000, "brl_t_mn": precos_kg_ms[i] * fracao_ms * 1000,
                       "brl_vaca_dia": kg * precos_kg_ms[i]})
    return pd.DataFrame(linhas).sort_values("kg_mn", ascending=False, ignore_index=True)


# --------------------------------------------------------------------------------------------------- gráficos
def grafico_dietas(dietas: dict, nomes: dict[int, str], destino: Path) -> Path:
    lotes = list(dietas)
    ids = sorted({int(i) for d in dietas.values() for i, v in d["percentual_ms"].items() if v > 0.05})
    fig, eixo = plt.subplots(figsize=(9, 4.8))
    base = np.zeros(len(lotes))
    cores = plt.get_cmap("tab20")(np.linspace(0, 1, max(len(ids), 2)))
    for cor, i in zip(cores, ids):
        valores = np.array([dietas[l]["percentual_ms"].get(str(i), 0.0) for l in lotes])
        eixo.bar(lotes, valores, bottom=base, label=nomes.get(i, str(i)), color=cor)
        base += valores
    eixo.set_ylabel("% da matéria seca")
    eixo.set_title("Dieta de custo mínimo por lote")
    eixo.legend(fontsize=8, bbox_to_anchor=(1.01, 1), loc="upper left")
    fig.tight_layout()
    fig.savefig(destino, dpi=130)
    plt.close(fig)
    return destino


def grafico_sobol(indices: pd.DataFrame, saida: str, destino: Path, titulo: str) -> Path:
    dados = indices[indices.saida == saida].sort_values("ST")
    fig, eixo = plt.subplots(figsize=(8, max(3, 0.32 * len(dados))))
    y = np.arange(len(dados))
    eixo.barh(y - 0.2, dados.S1.clip(lower=0), height=0.4, label="S1 (efeito isolado)", color="#2e7d32")
    eixo.barh(y + 0.2, dados.ST.clip(lower=0), height=0.4, label="ST (com interações)", color="#f9a825")
    eixo.set_yticks(y, dados.rotulo, fontsize=8)
    eixo.set_xlabel("Índice de Sobol")
    eixo.set_title(titulo)
    eixo.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(destino, dpi=130)
    plt.close(fig)
    return destino


def grafico_comparacao(tabela: pd.DataFrame, destino: Path) -> Path:
    dados = tabela.iloc[::-1]
    fig, eixo = plt.subplots(figsize=(8, max(3, 0.28 * len(dados))))
    eixo.barh(dados.caso, dados.pl_vs_ro_pct, color=["#2e7d32" if c else "#f9a825" for c in dados.ro_convergiu])
    eixo.axvline(0, color="#555555", linewidth=0.8)
    eixo.set_xlabel("Custo da PL acima do RationOptimizer (%)")
    eixo.set_title("PL × RationOptimizer do RuFaS no mesmo problema")
    eixo.tick_params(axis="y", labelsize=8)
    eixo.legend(handles=[Patch(color="#2e7d32", label="RationOptimizer convergiu"),
                         Patch(color="#f9a825", label="não convergiu (melhor execução viável)")], fontsize=8)
    fig.tight_layout()
    fig.savefig(destino, dpi=130)
    plt.close(fig)
    return destino


def grafico_pairwise(tabela: pd.DataFrame, destino: Path) -> Path:
    fig, eixo = plt.subplots(figsize=(9, 4))
    eixo.bar(tabela.cenario, tabela.iofc_brl, color="#2e7d32")
    eixo.set_ylabel("IOFC (R$/vaca/dia)")
    eixo.set_title("IOFC da vaca referência nos cenários pairwise")
    eixo.tick_params(axis="x", rotation=90, labelsize=8)
    fig.tight_layout()
    fig.savefig(destino, dpi=130)
    plt.close(fig)
    return destino


# ---------------------------------------------------------------------------------- leitura do quadro final
def leitura_negocio(faixa_rhs: pd.DataFrame, faixa_custos: pd.DataFrame, nomes: dict[int, str],
                    preco_leite: float, custo_marginal_leite: float | None,
                    minimos: dict[int, float] | None = None) -> list[str]:
    """Interpretação, em linguagem de gestão, das informações do quadro final do Simplex."""
    minimos = minimos or {}
    itens = []
    ativos = faixa_rhs[faixa_rhs.preco_sombra.abs() > 1e-9]
    folgados = faixa_rhs[(faixa_rhs.preco_sombra.abs() <= 1e-9) & (faixa_rhs.restricao.isin(ROTULO_NUTRIENTE))]

    for linha in ativos.itertuples():
        if linha.restricao in ROTULO_NUTRIENTE:
            rotulo, escala, _ = ROTULO_NUTRIENTE[linha.restricao]
            sentido = "a mais" if linha.sentido == ">=" else "a menos"
            itens.append(
                f"**{linha.descricao} é uma restrição ativa (gargalo).** Cada {rotulo} {sentido} na exigência custa "
                f"{_brl(abs(linha.preco_sombra) * escala, 4)} por vaca por dia (preço-sombra). Esse valor vale "
                f"enquanto a exigência ficar entre {_num(linha.valido_de)} e {_num(linha.valido_ate)} {linha.unidade}; "
                "fora dessa faixa a combinação de ingredientes muda e o preço-sombra precisa ser recalculado."
            )
        elif linha.restricao.startswith("max_"):
            i = int(linha.restricao.removeprefix("max_"))
            itens.append(
                f"**O limite de {nomes.get(i, i)} está travando a dieta.** Liberar 1 kg de MS a mais desse "
                f"ingrediente reduziria o custo em {_brl(abs(linha.preco_sombra), 4)} por vaca por dia "
                f"(até {_num(linha.valido_ate)} kg). Para 100 vacas, isso indica quanto vale ampliar silo, área ou "
                "contrato de fornecimento desse alimento."
            )
        elif linha.restricao.startswith("pmic"):
            itens.append(
                f"*{linha.descricao}* está ativa (preço-sombra {_brl(linha.preco_sombra, 5)} por g): a síntese de "
                "proteína microbiana no rúmen está no limite desse fator, então ingredientes que elevem esse limite "
                "barateiam a proteína metabolizável."
            )
    if not folgados.empty:
        lista = ", ".join(l.descricao for l in folgados.itertuples())
        itens.append(
            f"Restrições com folga (preço-sombra zero): {lista}. Os ingredientes que já entram na dieta atendem "
            "essas exigências com sobra, então exigir um pouco mais delas não aumenta o custo."
        )

    entram = faixa_custos[faixa_custos.situacao == "básica"]
    for linha in entram.itertuples():
        i = int(linha.rufas_id)
        itens.append(
            f"**{nomes.get(i, i)} está na dieta.** A receita ótima não muda enquanto o preço ficar entre "
            f"{_brl(max(linha.preco_min, 0))} e {_brl(linha.preco_max)} por kg de MS (intervalo de otimalidade); "
            "dentro dessa faixa só o custo total muda."
        )
    fora = faixa_custos[(faixa_custos.situacao == "não básica")].sort_values("custo_reduzido")
    for linha in fora.itertuples():
        i = int(linha.rufas_id)
        if minimos.get(i, 0.0) > 0:
            itens.append(
                f"**{nomes.get(i, i)} fica só no mínimo obrigatório ({_num(minimos[i])} kg MS).** Cada kg acima disso "
                f"custaria {_brl(linha.custo_reduzido, 4)} a mais por vaca por dia (custo reduzido); o mínimo é uma "
                "decisão técnica, não econômica. Só valeria usar mais se o preço caísse abaixo de "
                f"{_brl(linha.preco_min)} por kg de MS."
            )
            continue
        itens.append(
            f"**{nomes.get(i, i)} não compensa a esse preço.** Custo reduzido de {_brl(linha.custo_reduzido, 4)}/kg MS: "
            f"só entraria na dieta se o preço caísse de {_brl(linha.preco_atual)} para menos de "
            f"{_brl(linha.preco_min)} por kg de MS. É o preço-alvo para negociar com o fornecedor."
        )
    if custo_marginal_leite is not None and not np.isnan(custo_marginal_leite):
        margem = preco_leite - custo_marginal_leite
        itens.append(
            f"**Leite marginal:** produzir 1 kg a mais de leite exige {_brl(custo_marginal_leite, 3)} a mais de ração "
            f"(nova exigência NASEM resolvida pela PL). Com o leite a {_brl(preco_leite)}, cada kg adicional "
            f"{'aumenta' if margem > 0 else 'reduz'} o IOFC em {_brl(abs(margem), 3)}."
        )
    return itens


# ------------------------------------------------------------------------------ comparação com o RationOptimizer
RESTRICOES_RUFAS = {"protein_constraint_lower": ("proteína metabolizável", "g"), "phosphorus_constraint": ("fósforo", "g"),
                    "calcium_constraint": ("cálcio", "g"), "NASEM_net_energy_constraint": ("energia (ELl)", "Mcal")}


def _pct_com_sinal(valor: float) -> str:
    valor = 0.0 if abs(valor) < 0.005 else valor
    return f"{'+' if valor >= 0 else '−'}{_num(abs(valor))}%"


def secao_comparacao(pasta: Path, comp_arq: Path, dados: dict, figuras: Path) -> list[str]:
    """Seção 8: PL × RationOptimizer (SLSQP do RuFaS) no mesmo problema, a partir de ``comparacao_ration_optimizer.*``."""
    tabela = pd.read_csv(comp_arq)
    execucoes = pd.read_csv(pasta / "comparacao_ration_optimizer_execucoes.csv")
    with open(pasta / "comparacao_ration_optimizer.json", encoding="utf-8") as arquivo:
        extra = json.load(arquivo)
    n = extra["n_sementes"]
    md = ["## 8. PL × RationOptimizer do RuFaS no mesmo problema\n"]

    validos = tabela[tabela.ro_viavel & tabela.ro_convergiu]
    gap = validos.pl_vs_ro_pct
    abaixo, iguais = validos[gap <= -0.01], validos[gap.abs() < 0.01]
    perto, longe = validos[(gap >= 0.01) & (gap < 0.5)], validos[gap >= 0.5]
    partes = [f"chegou ao mesmo custo em {len(iguais)}"]
    if len(abaixo):
        partes.append(f"ficou abaixo em {len(abaixo)}")
    if len(perto):
        partes.append(f"ficou até {_num(perto.pl_vs_ro_pct.max())}% acima em {len(perto)}")
    if len(longe):
        partes.append(f"ficou entre {_num(longe.pl_vs_ro_pct.min(), 1)}% e {_num(longe.pl_vs_ro_pct.max(), 1)}% acima "
                      f"em {len(longe)} ({'; '.join(longe.caso)})")
    md.append(f"Nos {len(validos)} casos em que o RationOptimizer convergiu para uma dieta viável, a PL "
              + ", ".join(partes[:-1]) + (" e " if len(partes) > 1 else "") + partes[-1] + ".\n")
    grupos = tabela.grupo.value_counts()
    md.append(
        "Os dois otimizadores resolveram o mesmo problema: as mesmas exigências NASEM, os mesmos alimentos, preços e "
        "limites de inclusão, e as perdas de energia em metano e N urinário fixas na CMS prevista, como o "
        f"RationOptimizer as recebe na simulação. São {len(tabela)} casos: o caso de referência da aula, "
        f"{grupos.get('lote', 0)} lotes e {grupos.get('pairwise', 0)} cenários pairwise. O RationOptimizer sorteia o "
        f"ponto inicial, então cada caso rodou {n} vezes; vale a execução mais barata entre as viáveis (folga ≥ −0,001 "
        "em todas as restrições do RuFaS), preferindo as que o SciPy declarou convergidas.\n")

    grafico_comparacao(tabela, figuras / "comparacao_ration_optimizer.png")
    md.append("![PL × RationOptimizer](figuras/comparacao_ration_optimizer.png)\n")
    md.append("Custos por vaca por dia, em R$; no caso da aula, em US$. \"Dieta diferente\" é a parcela da MS que teria "
              "de trocar de ingrediente para passar de uma dieta à outra.\n")
    linhas = [[r.caso, _num(r.custo_pl, 4), _num(r.custo_ro, 4), _pct_com_sinal(r.pl_vs_ro_pct),
               f"{r.n_convergidas}/{r.n_execucoes}", f"{r.n_viaveis}/{r.n_execucoes}",
               f"{_num(r.diferenca_composicao_pct, 1)}%"] for r in tabela.itertuples()]
    md.append(_tabela_md(pd.DataFrame(linhas, columns=["Caso", "PL", "RationOptimizer", "PL − RO",
                                                       "RO convergiu", "RO viável", "Dieta diferente"])))
    md.append("")

    atende = int(validos.dieta_ro_atende_pl.sum())
    texto = (f"Em todos os {len(validos)} casos" if atende == len(validos) else f"Em {atende} dos {len(validos)} casos")
    texto = (
        f"**Por que a PL às vezes fica acima.** {texto}, a dieta do RationOptimizer atende todas as linhas da PL "
        "linearizadas nela mesma: a formulação é a mesma, e a diferença vem do método. A cada iteração a SLP recalcula "
        "os coeficientes que dependem da dieta inteira (energia digestível, desconto de NDT, degradabilidade da "
        "proteína) e os trata como constantes; ela não enxerga que trocar um ingrediente muda os coeficientes dos "
        "outros. Por isso pode parar num ponto fixo viável que não é o ótimo local do modelo não linear, que o SLSQP "
        "encontra usando as derivadas.")
    if len(longe):
        casos = list(longe.caso)
        nomes_casos = " e ".join([", ".join(casos[:-1]), casos[-1]]) if len(casos) > 1 else casos[0]
        texto += (f" O efeito aparece quando a dieta ótima muda muito: nos casos {nomes_casos}, "
                  f"{_num(longe.diferenca_composicao_pct.min(), 0)}% a {_num(longe.diferenca_composicao_pct.max(), 0)}% "
                  "da MS é diferente.")
    md.append(texto + " Dietas bem diferentes com custo quase igual indicam um ótimo \"plano\": várias combinações de "
              "ingredientes custam praticamente o mesmo.\n")

    convergidas, total = int(execucoes.convergiu.sum()), len(execucoes)
    texto = f"**O RationOptimizer depende do ponto inicial.** Ele convergiu em {convergidas} de {total} execuções."
    for r in tabela[~tabela.ro_convergiu].itertuples():
        do_caso = execucoes[execucoes.caso == r.caso]
        ruins = do_caso[do_caso.menor_folga < -1e-3]
        texto += (f" No {r.caso.split(' (')[0].lower()}, nenhuma das {len(do_caso)} execuções convergiu (o SLSQP "
                  f"para no limite de 100 iterações) e {len(ruins)} terminaram com restrições violadas")
        if len(ruins):
            pior = ruins.loc[ruins.menor_folga.idxmin()]
            nomes = [n.strip() for n in str(pior.restricoes_violadas).split(",")]
            if len(nomes) == 1 and nomes[0] in RESTRICOES_RUFAS:
                nome, unidade = RESTRICOES_RUFAS[nomes[0]]
                texto += f"; a pior ficou {_num(abs(pior.menor_folga), 0)} {unidade} abaixo da exigência de {nome}"
        texto += (f". A dieta comparada é a mais barata das {r.n_viaveis} que terminaram viáveis; na simulação, o RuFaS "
                  "trataria a não convergência como falha e reformularia com menos leite.")
    texto += (f" A PL resolveu todos os casos, em média em {_num(tabela.segundos_pl.mean(), 2)} s, contra "
              f"{_num(tabela.segundos_ro.mean(), 2)} s por execução do SLSQP.\n")
    md.append(texto)

    ref = dados["simplex"]["lote"]
    nome_ref, padrao = dados["lotes"][ref]["nome"], dados["lotes"][ref]["custo_brl"]
    linha_ref = tabela[tabela.caso == nome_ref]
    if not linha_ref.empty:
        md.append(f"**Formulação padrão do módulo.** As seções 2 a 7 usam perdas de energia proporcionais ao consumo "
                  "(o metano cresce com o que a vaca come). Com as perdas fixas desta comparação, a "
                  f"{nome_ref.lower()} custa {_brl(float(linha_ref.custo_pl.iloc[0]), 4)} em vez de {_brl(padrao, 4)} "
                  "por vaca por dia.\n")

    nomes = extra["nomes"]
    for caso in [tabela.caso.iloc[0], nome_ref]:
        if caso not in extra["dietas"]:
            continue
        d = extra["dietas"][caso]
        ids = sorted({i for lado in d.values() for i, v in lado.items() if v > 1e-3},
                     key=lambda i: -max(d["pl"].get(i, 0.0), d["ro"].get(i, 0.0)))
        md.append(f"Dietas do caso **{caso}** (kg MS/vaca/dia):\n")
        md.append(_tabela_md(pd.DataFrame(
            [[nomes.get(i, i), _num(d["pl"].get(i, 0.0)), _num(d["ro"].get(i, 0.0))] for i in ids]
            + [["**CMS total**", f"**{_num(sum(d['pl'].values()))}**", f"**{_num(sum(d['ro'].values()))}**"]],
            columns=["Ingrediente", "PL", "RationOptimizer"])))
        md.append("")
    return md


# --------------------------------------------------------------------------------------------------- relatório
def gerar_relatorio(config: ConfigOtimizacao) -> Path:
    pasta = config.dir_resultados
    otimizar, comparar = config.dir_etapa("otimizar"), config.dir_etapa("comparar")
    figuras = pasta / "figuras"
    figuras.mkdir(parents=True, exist_ok=True)
    with open(otimizar / "dietas.json", encoding="utf-8") as arquivo:
        dados = json.load(arquivo)
    nomes = {int(k): v for k, v in dados["nomes"].items()}
    dietas = dados["lotes"]
    md: list[str] = []
    add = md.append

    add(f"# Otimização de dieta — {config.fazenda}\n")
    add(f"Gerado em {datetime.now():%d/%m/%Y %H:%M} pelo módulo `otimizacao_dieta` (padrão {config.padrao_nutricional}, "
        f"preço do leite {_brl(config.preco_leite_brl_kg)}/kg, câmbio R$ {_num(config.cambio_brl_por_usd)}/US$).\n")
    add("> Preços são valores de referência para estudo. Atualize `config_otimizacao.json` com cotações locais.\n")

    add("## 1. Exigências por lote (NASEM 2021, calculadas pelo RuFaS)\n")
    linhas = []
    for lote in config.lotes:
        d = dietas[lote.id]
        e = d["exigencias"]
        linhas.append([lote.nome, lote.n_vacas, f"{_num(lote.peso_vivo, 0)} kg", lote.parto, lote.del_,
                       _num(lote.leite_kg, 1), _num(e["cms_kg"]), _num(e["nel_mcal"]), _num(e["pm_g"], 0),
                       _num(e["ca_g"], 1), _num(e["p_g"], 1)])
    add(_tabela_md(pd.DataFrame(linhas, columns=["Lote", "Vacas", "PV", "Parto", "DEL", "Leite (kg)", "CMS (kg)",
                                                 "ELl (Mcal)", "PM (g)", "Ca (g)", "P (g)"])))
    add("")

    add("## 2. Dieta de custo mínimo\n")
    grafico_dietas(dietas, nomes, figuras / "dietas_por_lote.png")
    add("![Dieta por lote](figuras/dietas_por_lote.png)\n")
    ids = sorted({int(i) for d in dietas.values() for i, v in d["kg_ms"].items() if v > 1e-4})
    linhas = []
    for i in ids:
        linha = [nomes.get(i, i), _brl(config.preco(i))]
        for lote in config.lotes:
            kg = dietas[lote.id]["kg_ms"].get(str(i), 0.0)
            linha.append(f"{_num(kg)} ({_num(dietas[lote.id]['percentual_ms'].get(str(i), 0.0), 1)}%)" if kg > 1e-4
                         else "—")
        linhas.append(linha)
    linhas.append(["**CMS total**", ""] + [f"**{_num(dietas[l.id]['cms_kg'])}**" for l in config.lotes])
    linhas.append(["**Custo (R$/vaca/dia)**", ""] + [f"**{_brl(dietas[l.id]['custo_brl'])}**" for l in config.lotes])
    add(_tabela_md(pd.DataFrame(linhas, columns=["Ingrediente (kg MS/vaca/dia)", "R$/kg MS"]
                                + [l.nome for l in config.lotes])))
    add("")
    lote_ref = config.lote(config.sensibilidade.get("lote", config.lotes[-1].id))
    kg_ref = {int(i): v for i, v in dietas[lote_ref.id]["kg_ms"].items()}
    biblioteca = ler_tabela(caminho_biblioteca(config))
    mn = dieta_materia_natural(kg_ref, {i: float(biblioteca.loc[i, "DM"]) for i in kg_ref},
                               {i: config.preco(i) for i in kg_ref})
    add(f"**{lote_ref.nome}: a dieta no formato do quadro da aula** (MN = matéria natural, o que vai no cocho):\n")
    linhas = [[nomes.get(r.rufas_id, r.rufas_id), f"{_num(r.ms_pct, 1)}%", _num(r.kg_ms), _num(r.kg_mn),
               _brl(r.brl_t_ms, 0), _brl(r.brl_t_mn, 0), _brl(r.brl_vaca_dia)] for r in mn.itertuples()]
    linhas.append(["**Total**", "", f"**{_num(mn.kg_ms.sum())}**", f"**{_num(mn.kg_mn.sum())}**", "", "",
                   f"**{_brl(mn.brl_vaca_dia.sum())}**"])
    add(_tabela_md(pd.DataFrame(linhas, columns=["Ingrediente", "MS do alimento", "kg MS/vaca/dia", "kg MN/vaca/dia",
                                                 "R$/t MS", "R$/t MN", "R$/vaca/dia"])))
    add("")
    add(f"No cocho, a ração pesa {_num(mn.kg_mn.sum(), 1)} kg por vaca por dia. A otimização é feita em matéria seca "
        "porque as exigências NASEM e a composição dos alimentos estão em base seca; o custo é o mesmo nas duas bases.\n")
    add("**Conferência pelo motor do RuFaS.** A dieta de cada lote foi avaliada pelas funções de restrição originais do "
        "`RationOptimizer` (NASEM, vaca em lactação). Valor ≥ 0 = atendida.\n")
    funcs = list(next(iter(dietas.values()))["validacao_rufas"])
    linhas = [[f] + [_num(dietas[l.id]["validacao_rufas"][f], 3) for l in config.lotes] for f in funcs]
    linhas.append(["**Todas atendidas?**"] + ["sim" if dietas[l.id]["validacao_ok"] else "**não**"
                                               for l in config.lotes])
    add(_tabela_md(pd.DataFrame(linhas, columns=["Restrição do RuFaS"] + [l.nome for l in config.lotes])))
    add("")
    if (comparar / "comparacao_ration_optimizer.csv").exists():
        add("A comparação com o otimizador original do RuFaS (RationOptimizer), resolvendo o mesmo problema, está na "
            "seção 8.\n")

    add("## 3. Avaliação econômica\n")
    economia = pd.read_csv(otimizar / "economia_lotes.csv")
    linhas = []
    for r in economia.itertuples():
        linhas.append([r.lote, r.cenario, _num(r.leite_kg_vaca_dia, 1), _num(r.cms_kg_vaca_dia),
                       _brl(r.custo_dieta_brl_vaca_dia), _brl(r.receita_leite_brl_vaca_dia), _brl(r.iofc_brl_vaca_dia),
                       _brl(r.custo_alimentar_brl_kg_leite, 3), r.atende_rufas])
    add(_tabela_md(pd.DataFrame(linhas, columns=["Lote", "Dieta", "Leite", "CMS", "Custo", "Receita", "IOFC",
                                                 "Custo/kg leite", "Atende o RuFaS?"])))
    add("")
    with open(otimizar / "economia_rebanho.json", encoding="utf-8") as arquivo:
        rebanho = json.load(arquivo)
    o, a = rebanho["otima"], rebanho["atual"]
    add(f"**Rebanho em lactação ({o['n_vacas']} vacas):** IOFC de {_brl(o['iofc_brl_dia'])}/dia com a dieta ótima "
        f"contra {_brl(a['iofc_brl_dia'])}/dia com a dieta atual do GO_campo_dourado, valorizada pelos mesmos preços. "
        f"Diferença de {_brl(o['iofc_brl_dia'] - a['iofc_brl_dia'])}/dia, ou "
        f"**{_brl(o['iofc_brl_ano'] - a['iofc_brl_ano'], 0)} por ano**. Custo alimentar: "
        f"{_brl(o['custo_alimentar_brl_kg_leite'], 3)}/kg de leite (ótima) × {_brl(a['custo_alimentar_brl_kg_leite'], 3)}"
        "/kg (atual).\n")
    if rebanho.get("atual_inviavel"):
        add("> Atenção: a dieta atual não atende todas as restrições do RuFaS em: "
            + "; ".join(f"{k}: {', '.join(v)}" for k, v in rebanho["atual_inviavel"].items())
            + ". No simulador, o RuFaS reduziria a produção de leite desses lotes; o IOFC acima assume a produção "
              "cheia e por isso favorece a dieta atual.\n")
    variantes_arq = otimizar / "economia_variantes.json"
    if variantes_arq.exists():
        with open(variantes_arq, encoding="utf-8") as arquivo:
            variantes = json.load(arquivo)
        rotulos = {"rufas_1_0_5": "RuFaS 1.0.5, como está", "sem_erro_gestacao": "Sem a exigência de gestação da vaca "
                   "não prenhe (provável erro do RuFaS 1.0.5)", "perdas_fixas": "Perdas de energia fixas na CMS prevista",
                   "cms_fixa": "CMS fixada na prevista (±0,1%)"}
        add("**A mesma comparação com uma hipótese trocada de cada vez** (economia potencial calculada pelo modelo, "
            "não economia observada):\n")
        add(_tabela_md(pd.DataFrame(
            [[rotulos.get(k, k), _brl(v["otima"]["custo_dieta_brl_dia"]), _brl(v["atual"]["custo_dieta_brl_dia"]),
              f"−{_num(v['reducao_custo_pct'])}%", _brl(v["diferenca_iofc_brl_ano"], 0),
              ", ".join(v["atual_inviavel"]) or "—"] for k, v in variantes.items()],
            columns=["Hipótese", "Custo ótima (R$/dia)", "Custo atual (R$/dia)", "Redução", "IOFC a mais por ano",
                     "Dieta atual falha em"])))
        add("")
    marg = pd.read_csv(otimizar / "custo_marginal_leite.csv")
    add("**Custo marginal do leite** (ração adicional para 1 kg a mais de leite, com a exigência recalculada):\n")
    add(_tabela_md(pd.DataFrame(
        [[r.lote, _brl(r.custo_marginal_brl_kg, 3), _brl(r.margem_marginal_brl_kg, 3)] for r in marg.itertuples()],
        columns=["Lote", "Custo marginal (R$/kg leite)", "Margem sobre o preço do leite"])))
    add("")

    add("## 4. Leitura do quadro final do Simplex (vaca referência)\n")
    add("A PL da última iteração foi resolvida também por um Simplex tabular de duas fases "
        "(`go_campo_dourado_ifg/otimizacao_dieta/simplex.py`), "
        f"que chegou ao mesmo ótimo do HiGHS ({_brl(dados['simplex']['objetivo'], 6)} × "
        f"{_brl(dados['simplex']['objetivo_highs'], 6)}). O quadro completo está em `quadro_final_simplex.csv`; abaixo, "
        "o que ele diz sobre o negócio.\n")
    rhs = pd.read_csv(otimizar / "simplex_faixas_restricoes.csv")
    rhs["descricao"] = [f"Limite máximo de {nomes.get(int(r.restricao.removeprefix('max_')), r.restricao)}"
                        if r.restricao.startswith("max_") else r.descricao for r in rhs.itertuples()]
    custos = pd.read_csv(otimizar / "simplex_faixas_precos.csv")
    with open(otimizar / "simplex_base.json", encoding="utf-8") as arquivo:
        base = json.load(arquivo)
    add("**Variáveis na base ótima** (as que têm valor no quadro final):\n")
    base = sorted(base, key=lambda b: (not b["variavel"].isdigit(), b["variavel"] == "m", b["variavel_rotulo"]))
    add(_tabela_md(pd.DataFrame([[b["variavel_rotulo"], _num(b["valor"], 4)] for b in base],
                                columns=["Variável básica", "Valor"])))
    add("")
    add("**Preços-sombra (duais) e faixa de validade:**\n")
    vis = rhs[(rhs.preco_sombra.abs() > 1e-9) | rhs.restricao.isin(ROTULO_NUTRIENTE)]
    add(_tabela_md(pd.DataFrame([[r.descricao, r.sentido, _num(r.lado_direito, 3), r.unidade, _num(r.preco_sombra, 5),
                                  _num(r.valido_de, 3), _num(r.valido_ate, 3)] for r in vis.itertuples()],
                                columns=["Restrição", "Sentido", "Lado direito", "Unid.", "Preço-sombra (R$/unid.)",
                                         "Válido de", "até"])))
    add("")
    add("**Intervalos de otimalidade dos preços e custos reduzidos:**\n")
    add(_tabela_md(pd.DataFrame([[nomes.get(int(r.rufas_id), r.rufas_id), r.situacao, _brl(r.preco_atual),
                                  _brl(r.preco_min) if np.isfinite(r.preco_min) else "−∞",
                                  _brl(r.preco_max) if np.isfinite(r.preco_max) else "+∞", _num(r.custo_reduzido, 4)]
                                 for r in custos.itertuples()],
                                columns=["Ingrediente", "Situação", "Preço", "Preço mín.", "Preço máx.",
                                         "Custo reduzido"])))
    add("")
    add("**Interpretação para a gestão:**\n")
    ref_marg = marg[marg.lote_id == config.sensibilidade.get("lote", "referencia")]
    cm = float(ref_marg.custo_marginal_brl_kg.iloc[0]) if not ref_marg.empty else None
    minimos = {i.rufas_id: i.min_kg_ms for i in config.ingredientes}
    for item in leitura_negocio(rhs, custos, nomes, config.preco_leite_brl_kg, cm, minimos):
        add(f"- {item}")
    add("")

    sobol_arq = config.dir_etapa("sensibilidade") / "sobol_indices.csv"
    if sobol_arq.exists():
        indices = pd.read_csv(sobol_arq)
        with open(sobol_arq.with_name("sobol_resumo.json"), encoding="utf-8") as arquivo:
            resumo = json.load(arquivo)
        add("## 5. Análise de sensibilidade de Sobol\n")
        add(f"{resumo['n_avaliacoes']} soluções da PL (Saltelli, N = {resumo['n_base']}, {resumo['k']} fatores; "
            f"{resumo['invalidas']} sem solução). Faixas: preços ±{config.sensibilidade.get('variacao_preco_pct', 25)}%, "
            "animal e preço do leite conforme `config_otimizacao.json`.\n")
        if "nao_atendem_rufas" in resumo:
            add(f"Cada dieta foi conferida nas restrições do RuFaS. A SLP não convergiu em {resumo['nao_convergiram']} "
                f"amostras (nelas o módulo fica com a iteração mais barata que atende o RuFaS, se houver); "
                f"{resumo['nao_atendem_rufas']} dietas não atendem alguma restrição, com folga de até "
                f"{_num(resumo['pior_folga_pct_exigencia'])}% da exigência (mediana "
                f"{_num(resumo['mediana_folga_pct_exigencia'])}%). A coluna \"ST (só válidas)\" repete o índice dando "
                "a essas amostras a média das válidas.\n")
        for saida, titulo in [("custo_dieta_brl", "Custo da dieta"), ("iofc_brl", "IOFC")]:
            arq = figuras / f"sobol_{saida}.png"
            grafico_sobol(indices, saida, arq, f"Sobol — {titulo}")
            add(f"![Sobol {titulo}](figuras/{arq.name})\n")
        for saida, titulo in [("custo_dieta_brl", "custo da dieta"), ("iofc_brl", "IOFC")]:
            top = indices[indices.saida == saida].sort_values("ST", ascending=False).head(6)
            add(f"Fatores que mais explicam a variância do **{titulo}**:\n")
            validas = "ST_validas" in top.columns
            add(_tabela_md(pd.DataFrame([[r.rotulo, _num(r.S1, 3), _num(r.ST, 3), _num(r.ST - r.S1, 3)]
                                         + ([_num(r.ST_validas, 3)] if validas else [])
                                         for r in top.itertuples()],
                                        columns=["Fator", "S1", "ST", "Interações (ST − S1)"]
                                        + (["ST (só válidas)"] if validas else []))))
            add("")

    pw_arq = config.dir_etapa("pairwise") / "pairwise_cenarios.csv"
    if pw_arq.exists():
        tabela = pd.read_csv(pw_arq)
        with open(pw_arq.with_name("pairwise_resumo.json"), encoding="utf-8") as arquivo:
            resumo = json.load(arquivo)
        add("## 6. Cenários por pairwise testing\n")
        add(f"{len(tabela)} cenários cobrem todos os pares de níveis (baixo/base/alto) de {len(resumo['fatores'])} fatores "
            f"({', '.join(resumo['rotulos'])}), contra {resumo['fatorial_completo']} do fatorial completo. "
            f"Pares sem cobertura: {resumo['pares_descobertos']}.\n")
        grafico_pairwise(tabela, figuras / "pairwise_iofc.png")
        add("![IOFC pairwise](figuras/pairwise_iofc.png)\n")
        colunas = ["cenario"] + resumo["fatores"] + ["custo_dieta_brl", "iofc_brl", "custo_kg_leite_brl"]
        vis = tabela[colunas].copy()
        for c in ["custo_dieta_brl", "iofc_brl"]:
            vis[c] = vis[c].map(_brl)
        vis["custo_kg_leite_brl"] = vis["custo_kg_leite_brl"].map(lambda v: _brl(v, 3))
        vis.columns = ["Cenário"] + resumo["rotulos"] + ["Custo", "IOFC", "Custo/kg leite"]
        add(_tabela_md(vis))
        add("")
        add(f"IOFC entre {_brl(tabela.iofc_brl.min())} e {_brl(tabela.iofc_brl.max())} por vaca por dia: é a faixa "
            "de resultado que o planejamento precisa suportar.\n")
        if "nao_atendem_rufas" in resumo:
            add(f"Dietas que não atendem alguma restrição do RuFaS: {resumo['nao_atendem_rufas']} de {len(tabela)}; "
                f"SLP sem convergência: {resumo['nao_convergiram']} (no máximo {resumo['max_iteracoes_slp']} "
                "iterações).\n")

    rufas_arq = config.dir_etapa("rufas") / "rufas_comparacao.json"
    if rufas_arq.exists():
        with open(rufas_arq, encoding="utf-8") as arquivo:
            comp = json.load(arquivo)
        add("## 7. Simulação completa no RuFaS (rebanho inteiro, vários anos)\n")
        add("Simulação pareada: a fazenda roda com a dieta atual e com a ração média ótima no curral de lactação, com as "
            "mesmas sementes (`random_seeds`), a mesma tabela de preços em todos os currais e as mesmas demais entradas. "
            "Com a mesma semente o RuFaS repete a simulação, então a diferença dentro de cada par vem da ração. As "
            "rodadas são valorizadas com a tabela de preços de Goiás.\n")
        for d in comp.get("sementes_descartadas", []):
            add(f"Semente {d['semente']} descartada, porque uma simulação do par parou no RuFaS: "
                + "; ".join(f"{c}: `{e}`" for c, e in d["erros"].items()) + "\n")
        add(f"Ração gravada no curral de lactação (% MS): "
            + ", ".join(f"{nomes.get(int(i), i)} {_num(p, 2)}%" for i, p in comp["racao_lactacao"].items()) + "\n")
        chaves = [("leite_total_kg", "Leite total (kg)", 0, False),
                  ("leite_kg_vaca_lactante_dia", "Leite por vaca lactante/dia (kg)", 2, False),
                  ("vacas_lactantes_media", "Vacas em lactação (média)", 1, False),
                  ("custo_alimentacao_total_brl", "Custo de alimentação total", 0, True),
                  ("iofc_brl_ano", "IOFC por ano", 0, True),
                  ("custo_alimentar_brl_kg_leite", "Custo alimentar por kg de leite", 3, True)]
        for par in comp.get("pares", [{"semente": "—", "base": comp["base"], "ifg": comp["ifg"]}]):
            linhas = []
            for chave, rotulo, casas, dinheiro in chaves:
                f = (lambda v, c=casas: _brl(v, c)) if dinheiro else (lambda v, c=casas: _num(v, c))
                b, c = par["base"][chave], par["ifg"][chave]
                linhas.append([rotulo, f(b), f(c), f(c - b)])
            add(f"Semente {par['semente']} ({par['ifg']['dias_simulados']} dias):\n")
            add(_tabela_md(pd.DataFrame(linhas, columns=["Indicador", "Dieta atual", "Dieta ótima", "Diferença"])))
            add("")
        if "resumo" in comp:
            d = comp["resumo"]["iofc_brl_ano"]
            add(f"**Diferença de IOFC por ano (ótima − atual), {d['n']} pares:** média {_brl(d['media'], 0)}, de "
                f"{_brl(d['min'], 0)} a {_brl(d['max'], 0)}; intervalo de 95% (t de Student): {_brl(d['ic95_inf'], 0)} a "
                f"{_brl(d['ic95_sup'], 0)}.\n")
        add("O custo inclui toda a alimentação do rebanho (bezerras, novilhas, pré-parto e lactação). A diferença de leite "
            "reflete a resposta do RuFaS à nova dieta (redução de leite quando algum nutriente fica abaixo da exigência "
            "de uma vaca individual).\n")

    comp_arq = comparar / "comparacao_ration_optimizer.csv"
    if comp_arq.exists():
        md.extend(secao_comparacao(comparar, comp_arq, dados, figuras))

    add("## Limitações\n")
    add("- A PL é a linearização do modelo NASEM do RuFaS no ponto ótimo. Preços-sombra e intervalos valem perto desse "
        "ponto; para mudanças grandes, a otimização é refeita (é o que a SLP, o Sobol e o pairwise fazem).\n"
        "- O limite superior de proteína metabolizável usa o ramo do NDT da proteína microbiana, que é conservador.\n"
        "- A SLP trata os coeficientes da iteração como constantes e pode parar num ponto fixo viável um pouco mais caro "
        "que o ótimo local do modelo não linear do RuFaS (seção 8), ou oscilar sem convergir; nesse caso o módulo fica "
        "com a iteração mais barata que atende o RuFaS e marca a dieta. \"Ótima\" quer dizer a de menor custo que a "
        "SLP encontrou.\n"
        "- A otimização principal desconta as perdas de energia em metano e N urinário proporcionais ao consumo; o "
        "RationOptimizer as recebe fixas. A variante com perdas fixas está na seção 3.\n"
        "- Metano entérico e N urinário por kg de CMS foram calibrados na rodada base do GO_campo_dourado (3-NOP ativo).\n"
        "- A biblioteca NASEM do RuFaS não tem bagaço de cana; ele aparece no quadro da aula mas ficou de fora.\n"
        "- O RuFaS usa uma ração por curral; os lotes do quadro são agregados numa ração média para a simulação.\n"
        "- Provável erro no RuFaS 1.0.5: vaca não prenhe com DEL < 100 recebe exigência de ELl de 'gestação' que cresce "
        "com o DEL (7,6 Mcal no DEL 45; 16,8 Mcal no DEL 99) e zera no DEL 100. O lote de início de lactação herda esse "
        "acréscimo, porque o módulo usa os calculadores do RuFaS sem alteração; a seção 3 mostra a economia sem ele. A "
        "simulação do RuFaS continua com o cálculo original.\n")

    destino = pasta / "relatorio_otimizacao.md"
    destino.write_text("\n".join(md), encoding="utf-8")
    return destino
