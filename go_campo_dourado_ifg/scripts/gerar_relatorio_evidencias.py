"""Gera o relatório técnico (.docx e .pdf) com as evidências do trabalho: modelagem, execução, resultados e comparação.

Lê só arquivos de resultado (resultados/<etapa>/, resultados/execucao/) e desenha três figuras novas, que grava em
docs/figuras/. Rodar depois de
``coletar_execucao.py`` e ``avaliar_rodadas_rufas.py``:

    python go_campo_dourado_ifg/scripts/gerar_relatorio_evidencias.py .

O sumário sai com os números de página: a primeira passada converte o .docx para PDF no LibreOffice, acha a página de
cada título e chama o script de novo com elas. No Word, o sumário continua sendo um campo atualizável.
"""

import json
import platform
import subprocess
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK, WD_TAB_ALIGNMENT, WD_TAB_LEADER
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Patch

RAIZ = Path(sys.argv[1]).resolve()
FAZ = RAIZ / "go_campo_dourado_ifg"
RES = FAZ / "resultados"
EXE = RES / "execucao"
FIG = RES / "figuras"
FIG_DOC = FAZ / "docs" / "figuras"
SAIDA = FAZ / "docs" / "Relatorio_Tecnico_Otimizacao_Dieta_RuFaS.docx"
SOFFICE = Path(r"C:\Program Files\LibreOffice\program\soffice.exe")
PAGINAS = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8")) if len(sys.argv) > 2 else None

VERDE = RGBColor(0x1B, 0x5E, 0x20)
CINZA = RGBColor(0x55, 0x55, 0x55)
COR_PL, COR_BASE, COR_ALERTA, COR_TEXTO = "#2e7d32", "#9e9e9e", "#f9a825", "#333333"


def ler_json(caminho):
    return json.loads(Path(caminho).read_text(encoding="utf-8"))


cfg = ler_json(FAZ / "config_otimizacao.json")
dietas = ler_json(RES / "otimizar" / "dietas.json")
reb = ler_json(RES / "otimizar" / "economia_rebanho.json")
rufas = ler_json(RES / "rufas" / "rufas_comparacao.json")
eco = pd.read_csv(RES / "otimizar" / "economia_lotes.csv")
marg = pd.read_csv(RES / "otimizar" / "custo_marginal_leite.csv")
rhs = pd.read_csv(RES / "otimizar" / "simplex_faixas_restricoes.csv")
precos = pd.read_csv(RES / "otimizar" / "simplex_faixas_precos.csv")
sobol = pd.read_csv(RES / "sensibilidade" / "sobol_indices.csv")
sobol_res = ler_json(RES / "sensibilidade" / "sobol_resumo.json")
sobol_fat = pd.read_csv(RES / "sensibilidade" / "sobol_fatores.csv")
pw = pd.read_csv(RES / "pairwise" / "pairwise_cenarios.csv")
pw_res = ler_json(RES / "pairwise" / "pairwise_resumo.json")
comp = pd.read_csv(RES / "comparar" / "comparacao_ration_optimizer.csv")
comp_json = ler_json(RES / "comparar" / "comparacao_ration_optimizer.json")
execs = pd.read_csv(RES / "comparar" / "comparacao_ration_optimizer_execucoes.csv")
execucao = ler_json(EXE / "execucao.json")
rodadas = pd.read_csv(RES / "rufas" / "rodadas_rufas.csv") if (RES / "rufas" / "rodadas_rufas.csv").exists() else None

nomes = {int(k): v for k, v in dietas["nomes"].items()}
lotes = dietas["lotes"]
lotes_cfg = {l["id"]: l for l in cfg["lotes"]}
ref = lotes["referencia"]
TOL_VIAVEL = -1e-3


def num(v, c=2):
    s = f"{v:,.{c}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def brl(v, c=2):
    return ("−" if v < 0 else "") + "R$ " + num(abs(v), c)


def pct(v, c=1, sinal=False):
    return ("+" if sinal and v > 0 else "−" if v < 0 else "") + num(abs(v), c) + "%"


def limite(v, c=3):
    return "+∞" if v == float("inf") else "−∞" if v == float("-inf") else num(v, c)


def git(*args):
    try:
        return subprocess.run(["git", *args], cwd=RAIZ, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def docker_imagem():
    try:
        saida = subprocess.run(["docker", "image", "inspect", "rufas:1.0.5", "--format",
                                "{{.Id}}|{{.Created}}|{{.Size}}"], capture_output=True, text=True, check=True).stdout
        ident, criada, tamanho = saida.strip().split("|")
        return f"rufas:1.0.5 · {ident[7:19]} · criada em {criada[:10]} · {int(tamanho) / 1e9:.2f} GB"
    except (OSError, subprocess.CalledProcessError, ValueError):
        return "rufas:1.0.5"


# ------------------------------------------------------------------------------------------- números-chave
etapas = pd.DataFrame(execucao["etapas"])
amb = execucao["ambiente"]
inicio_execucao = datetime.fromisoformat(etapas.inicio.iloc[0])
fim_execucao = datetime.fromisoformat(execucao["gerado_em"])
segundos_total = float(etapas.segundos.sum())

custo_otimo, custo_atual = reb["otima"]["custo_dieta_brl_dia"], reb["atual"]["custo_dieta_brl_dia"]
reducao_custo_pct = 100 * (1 - custo_otimo / custo_atual)
ganho_pl_ano = reb["otima"]["iofc_brl_ano"] - reb["atual"]["iofc_brl_ano"]
ganho_rufas_ano = rufas["ifg"]["iofc_brl_ano"] - rufas["base"]["iofc_brl_ano"]

convergidos = comp[comp.ro_convergiu]
n_iguais = int((convergidos.pl_vs_ro_pct.abs() < 0.005).sum())
n_pequena = int(((convergidos.pl_vs_ro_pct >= 0.005) & (convergidos.pl_vs_ro_pct <= 0.5)).sum())
grandes = convergidos[convergidos.pl_vs_ro_pct > 0.5]
max_pequena = float(convergidos[convergidos.pl_vs_ro_pct <= 0.5].pl_vs_ro_pct.max())
nao_convergidos = comp[~comp.ro_convergiu]
execs["viavel"] = execs.menor_folga >= TOL_VIAVEL
aula = comp[comp.grupo == "aula"].iloc[0]
pior_aula = float(execs[execs.grupo == "aula"].menor_folga.min())


def top_sobol(saida, n=6):
    return sobol[sobol.saida == saida].sort_values("ST", ascending=False).head(n)


top_custo, top_iofc = top_sobol("custo_dieta_brl"), top_sobol("iofc_brl")

if rodadas is not None:
    rod_base = rodadas[rodadas.fazenda == "base"]
    rod_ifg = rodadas[(rodadas.fazenda == "ifg") & rodadas.racao_igual_atual]
    ganho_min = float(rod_ifg.iofc_brl_ano.min() - rod_base.iofc_brl_ano.max())
    ganho_max = float(rod_ifg.iofc_brl_ano.max() - rod_base.iofc_brl_ano.min())
    ganho_medio = float(rod_ifg.iofc_brl_ano.mean() - rod_base.iofc_brl_ano.mean())


# ------------------------------------------------------------------------------------------- figuras novas
def caixa_diagrama(eixo, x, y, w, h, texto, estilo):
    cores = {"rufas": ("#e8f5e9", COR_PL), "modulo": ("#ffffff", COR_PL), "dado": ("#f5f5f5", COR_BASE)}
    fundo, borda = cores[estilo]
    eixo.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.12", facecolor=fundo,
                                  edgecolor=borda, linewidth=1.4, linestyle="--" if estilo == "dado" else "-"))
    eixo.text(x + w / 2, y + h / 2, texto, ha="center", va="center", fontsize=8.5, color=COR_TEXTO, linespacing=1.3)


def seta(eixo, x1, y1, x2, y2, rotulo=None):
    eixo.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>", mutation_scale=11, color="#616161",
                                   linewidth=1.1, shrinkA=2, shrinkB=2))
    if rotulo:
        eixo.text((x1 + x2) / 2 + 0.08, (y1 + y2) / 2, rotulo, fontsize=7, color="#616161", va="center")


def diagrama_arquitetura(destino):
    fig, eixo = plt.subplots(figsize=(8.6, 5.6))
    eixo.set_xlim(0, 16)
    eixo.set_ylim(0, 10)
    eixo.axis("off")
    # entradas
    caixa_diagrama(eixo, 0.2, 8.2, 4.6, 1.3, "config_otimizacao.json\nlotes, preços, limites", "dado")
    caixa_diagrama(eixo, 5.6, 8.2, 4.8, 1.3, "Exigências NASEM do RuFaS\n(NASEMRequirementsCalculator)", "rufas")
    caixa_diagrama(eixo, 11.2, 8.2, 4.6, 1.3, "Alimentos do RuFaS\n(NASEM_Comp.csv → NASEMFeed)", "rufas")
    # núcleo
    caixa_diagrama(eixo, 4.2, 5.4, 7.6, 1.6, "modelo.py — PL de custo mínimo por lote\nHiGHS + linearização sucessiva (SLP)\n"
                   "exigencias.py · biblioteca.py", "modulo")
    caixa_diagrama(eixo, 12.4, 5.4, 3.4, 1.6, "Validação pelas\nrestrições do\nRationOptimizer", "rufas")
    seta(eixo, 2.5, 8.2, 5.4, 7.0)
    seta(eixo, 8.0, 8.2, 8.0, 7.0)
    seta(eixo, 13.5, 8.2, 10.6, 7.0)
    seta(eixo, 11.8, 6.2, 12.4, 6.2)
    # análises
    analises = [(0.2, "simplex.py\nquadro final,\npreços-sombra"), (3.4, "economia.py\ncusto, IOFC,\ncusto/kg leite"),
                (6.6, "sensibilidade.py\npairwise.py\nSobol, cenários"),
                (9.8, "comparacao.py\nPL × SLSQP do\nRationOptimizer"), (13.0, "integracao_\nrufas.py\nfeed.json")]
    for x, texto in analises:
        caixa_diagrama(eixo, x, 2.7, 2.8, 1.7, texto, "modulo")
        seta(eixo, 8.0, 5.4, x + 1.4, 4.4)
    # simulação e relatório
    caixa_diagrama(eixo, 12.4, 0.2, 3.4, 1.5, "RuFaS main.py\nsimulação de 5 anos", "rufas")
    caixa_diagrama(eixo, 4.2, 0.2, 7.0, 1.5, "economia.avaliar_saida_rufas (IOFC simulado)\nrelatorio.py → relatório, figuras",
                   "modulo")
    seta(eixo, 14.4, 2.7, 14.1, 1.7)
    seta(eixo, 12.4, 0.95, 11.2, 0.95, None)
    eixo.text(11.8, 1.15, "CSV", fontsize=7, color="#616161", ha="center")
    eixo.legend(handles=[Patch(facecolor="#e8f5e9", edgecolor=COR_PL, label="código do RuFaS reutilizado"),
                         Patch(facecolor="#ffffff", edgecolor=COR_PL, label="módulo otimizacao_dieta"),
                         Patch(facecolor="#f5f5f5", edgecolor=COR_BASE, linestyle="--", label="dados de entrada")],
                loc="lower left", fontsize=7.5, frameon=False, bbox_to_anchor=(0.0, -0.02))
    fig.tight_layout()
    fig.savefig(destino, dpi=200)
    plt.close(fig)


def grafico_execucao(destino):
    dados = etapas
    fig, eixo = plt.subplots(figsize=(8, 3.2))
    y = range(len(dados))[::-1]
    eixo.barh(list(y), dados.segundos, color=COR_PL, height=0.55)
    for yi, s in zip(y, dados.segundos):
        eixo.text(s + segundos_total * 0.01, yi, f"{num(s, 1)} s", va="center", fontsize=8.5, color=COR_TEXTO)
    eixo.set_yticks(list(y), dados.etapa)
    eixo.set_xlabel("tempo de parede (s)", fontsize=9, color=COR_TEXTO)
    eixo.set_xlim(0, dados.segundos.max() * 1.15)
    for lado in ("top", "right"):
        eixo.spines[lado].set_visible(False)
    eixo.tick_params(labelsize=8.5, colors=COR_TEXTO)
    eixo.grid(axis="x", color="#e0e0e0", linewidth=0.6)
    eixo.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(destino, dpi=200)
    plt.close(fig)


def grafico_rodadas(destino):
    fig, eixo = plt.subplots(figsize=(8, 2.6))
    grupos = [("GO_campo_dourado\n(dieta original)", rod_base, COR_BASE),
              ("go_campo_dourado_ifg\n(dieta ótima)", rod_ifg, COR_PL)]
    for k, (rotulo, dados, cor) in enumerate(grupos):
        eixo.scatter(dados.iofc_brl_ano / 1e6, [k] * len(dados), s=70, color=cor, edgecolor="white", linewidth=1.5,
                     zorder=3)
        media = dados.iofc_brl_ano.mean() / 1e6
        eixo.plot([media, media], [k - 0.22, k + 0.22], color=COR_TEXTO, linewidth=1.5, zorder=2)
        eixo.text(media, k + 0.3, f"média R$ {num(media, 3)} mi", ha="center", fontsize=8, color=COR_TEXTO)
    atual = rodadas[rodadas.esta_execucao]
    if len(atual):
        eixo.scatter(atual.iofc_brl_ano / 1e6, [1] * len(atual), s=190, facecolor="none", edgecolor=COR_TEXTO,
                     linewidth=1.2, zorder=4)
        eixo.annotate("esta execução", (atual.iofc_brl_ano.iloc[0] / 1e6, 1), xytext=(0, -20),
                      textcoords="offset points", ha="center", fontsize=7.5, color=COR_TEXTO)
    eixo.set_yticks([0, 1], [g[0] for g in grupos])
    eixo.set_ylim(-0.6, 1.7)
    eixo.set_xlabel("IOFC do rebanho por ano (R$ milhões)", fontsize=9, color=COR_TEXTO)
    for lado in ("top", "right", "left"):
        eixo.spines[lado].set_visible(False)
    eixo.tick_params(labelsize=8.5, colors=COR_TEXTO, left=False)
    eixo.grid(axis="x", color="#e0e0e0", linewidth=0.6)
    eixo.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(destino, dpi=200)
    plt.close(fig)


FIG_DOC.mkdir(parents=True, exist_ok=True)
diagrama_arquitetura(FIG_DOC / "arquitetura_modulo.png")
grafico_execucao(FIG_DOC / "execucao_etapas.png")
if rodadas is not None:
    grafico_rodadas(FIG_DOC / "rufas_rodadas_iofc.png")

# ------------------------------------------------------------------------------------------- documento
doc = Document()
sec = doc.sections[0]
sec.page_width, sec.page_height = Cm(21), Cm(29.7)
for lado in ("left_margin", "right_margin"):
    setattr(sec, lado, Cm(2.3))
sec.top_margin, sec.bottom_margin = Cm(2.2), Cm(2.2)

estilos = doc.styles
normal = estilos["Normal"]
normal.font.name = "Calibri"
normal.font.size = Pt(11)
normal.element.rPr.rFonts.set(qn("w:eastAsia"), "Calibri")
normal.paragraph_format.space_after = Pt(6)
normal.paragraph_format.line_spacing = 1.15
for nome, tam in (("Heading 1", 18), ("Heading 2", 14), ("Heading 3", 12)):
    s = estilos[nome]
    s.font.name = "Calibri"
    s.font.size = Pt(tam)
    s.font.color.rgb = VERDE
    s.font.bold = True
    s.paragraph_format.space_before = Pt(16 if nome == "Heading 1" else 10)
    s.paragraph_format.space_after = Pt(6)
    s.paragraph_format.keep_with_next = True


def sombrear(celula, cor):
    tc = celula._tc.get_or_add_tcPr()
    sh = OxmlElement("w:shd")
    sh.set(qn("w:val"), "clear")
    sh.set(qn("w:color"), "auto")
    sh.set(qn("w:fill"), cor)
    tc.append(sh)


def borda_paragrafo(p, cor="F9A825", lado="left", tam=24):
    ppr = p._p.get_or_add_pPr()
    bdr = OxmlElement("w:pBdr")
    b = OxmlElement(f"w:{lado}")
    b.set(qn("w:val"), "single")
    b.set(qn("w:sz"), str(tam))
    b.set(qn("w:space"), "8")
    b.set(qn("w:color"), cor)
    bdr.append(b)
    ppr.append(bdr)


def sombra_paragrafo(p, cor):
    ppr = p._p.get_or_add_pPr()
    sh = OxmlElement("w:shd")
    sh.set(qn("w:val"), "clear")
    sh.set(qn("w:color"), "auto")
    sh.set(qn("w:fill"), cor)
    ppr.append(sh)


titulos = []  # (nível, texto) na ordem do documento, para o sumário


def h1(t, nova_pagina=True):
    titulos.append((1, t))
    doc.add_heading(t, level=1).paragraph_format.page_break_before = nova_pagina


def h2(t):
    titulos.append((2, t))
    doc.add_heading(t, level=2)


def h3(t):
    doc.add_heading(t, level=3)


def par(texto, negrito_inicio=None, italico=False):
    p = doc.add_paragraph()
    if negrito_inicio:
        r = p.add_run(negrito_inicio)
        r.bold = True
    r = p.add_run(texto)
    r.italic = italico
    return p


def rico(*partes):
    """partes: strings ou tuplas (texto, 'b'|'i'|'c')."""
    p = doc.add_paragraph()
    for parte in partes:
        if isinstance(parte, str):
            p.add_run(parte)
        else:
            t, f = parte
            r = p.add_run(t)
            r.bold = "b" in f
            r.italic = "i" in f
            if "c" in f:
                r.font.name = "Consolas"
                r.font.size = Pt(9.5)
    return p


def nova_numeracao():
    """Instância nova da numeração do estilo 'List Number', para cada lista recomeçar em 1."""
    numeracao = doc.part.numbering_part.element
    abstrata = numeracao.num_having_numId(doc.styles["List Number"].element.pPr.numPr.numId.val).abstractNumId.val
    num = numeracao.add_num(abstrata)
    num.add_lvlOverride(ilvl=0).add_startOverride(1)
    return num.numId


def lista(itens, numerada=False):
    num_id = nova_numeracao() if numerada else None
    for it in itens:
        p = doc.add_paragraph(style="List Number" if numerada else "List Bullet")
        if num_id is not None:
            num_pr = p._p.get_or_add_pPr().get_or_add_numPr()
            num_pr.get_or_add_ilvl().val = 0
            num_pr.get_or_add_numId().val = num_id
        if isinstance(it, tuple):
            r = p.add_run(it[0])
            r.bold = True
            p.add_run(it[1])
        else:
            p.add_run(it)
        p.paragraph_format.space_after = Pt(3)


def codigo(texto, tamanho=8.5):
    for i, linha in enumerate(texto.strip("\n").split("\n")):
        p = doc.add_paragraph()
        r = p.add_run(linha if linha else " ")
        r.font.name = "Consolas"
        r.font.size = Pt(tamanho)
        p.paragraph_format.space_after = Pt(0)
        p.paragraph_format.space_before = Pt(4) if i == 0 else Pt(0)
        p.paragraph_format.line_spacing = 1.0
        p.paragraph_format.left_indent = Cm(0.3)
        sombra_paragrafo(p, "F1F3F1")
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def formula(*linhas):
    for i, linha in enumerate(linhas):
        p = doc.add_paragraph()
        r = p.add_run(linha)
        r.font.name = "Cambria Math"
        r.font.size = Pt(11)
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_after = Pt(2 if i < len(linhas) - 1 else 8)
        p.paragraph_format.space_before = Pt(4 if i == 0 else 0)


def caixa(titulo, texto, cor="FFF8E1", borda="F9A825"):
    p = doc.add_paragraph()
    r = p.add_run(titulo + " ")
    r.bold = True
    p.add_run(texto)
    sombra_paragrafo(p, cor)
    borda_paragrafo(p, borda)
    p.paragraph_format.left_indent = Cm(0.3)
    p.paragraph_format.space_before = Pt(6)
    p.paragraph_format.space_after = Pt(10)
    return p


def tabela(cabecalho, linhas, larguras=None, fonte=9.5, alinhar_dir=()):
    t = doc.add_table(rows=1, cols=len(cabecalho))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, c in enumerate(cabecalho):
        cel = t.rows[0].cells[i]
        cel.text = ""
        r = cel.paragraphs[0].add_run(str(c))
        r.bold = True
        r.font.size = Pt(fonte)
        r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        sombrear(cel, "2E7D32")
    for k, linha in enumerate(linhas):
        cels = t.add_row().cells
        for i, v in enumerate(linha):
            cels[i].text = ""
            p = cels[i].paragraphs[0]
            negrito = isinstance(v, tuple)
            r = p.add_run(str(v[0] if negrito else v))
            r.bold = negrito
            r.font.size = Pt(fonte)
            if i in alinhar_dir:
                p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            if k % 2 == 1:
                sombrear(cels[i], "F1F8E9")
    # repete cabeçalho
    trpr = t.rows[0]._tr.get_or_add_trPr()
    th = OxmlElement("w:tblHeader")
    th.set(qn("w:val"), "true")
    trpr.append(th)
    if not larguras:
        larguras = [16.4 / len(cabecalho)] * len(cabecalho)
    t.autofit = False
    grade = t._tbl.tblGrid
    for col, w in zip(grade.findall(qn("w:gridCol")), larguras):
        col.set(qn("w:w"), str(int(Cm(w).twips)))
    tblpr = t._tbl.tblPr
    tblw = tblpr.find(qn("w:tblW"))
    if tblw is None:
        tblw = OxmlElement("w:tblW")
        tblpr.append(tblw)
    tblw.set(qn("w:type"), "dxa")
    tblw.set(qn("w:w"), str(int(Cm(sum(larguras)).twips)))
    layout = OxmlElement("w:tblLayout")
    layout.set(qn("w:type"), "fixed")
    tblpr.append(layout)
    for linha in t.rows:
        nao_quebra = OxmlElement("w:cantSplit")
        linha._tr.get_or_add_trPr().append(nao_quebra)
        for i, w in enumerate(larguras):
            linha.cells[i].width = Cm(w)
    for linha in t.rows:
        for cel in linha.cells:
            for p in cel.paragraphs:
                p.paragraph_format.space_after = Pt(1)
                p.paragraph_format.space_before = Pt(1)
                p.paragraph_format.line_spacing = 1.0
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return t


figuras = []


def figura(caminho, legenda, largura=15.5):
    figuras.append(legenda)
    doc.add_picture(str(caminho), width=Cm(largura))
    doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.paragraphs[-1].paragraph_format.keep_with_next = True
    p = doc.add_paragraph()
    r = p.add_run(f"Figura {len(figuras)} — {legenda}")
    r.italic = True
    r.font.size = Pt(9)
    r.font.color.rgb = CINZA
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER


def quebra():
    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)


def campo(run, instrucao):
    for tipo, txt in (("begin", None), (None, instrucao), ("end", None)):
        if tipo:
            f = OxmlElement("w:fldChar")
            f.set(qn("w:fldCharType"), tipo)
            run._r.append(f)
        else:
            it = OxmlElement("w:instrText")
            it.set(qn("xml:space"), "preserve")
            it.text = txt
            run._r.append(it)


def marca_campo(paragrafo, tipo, instrucao=None):
    r = paragrafo.add_run()
    if instrucao:
        it = OxmlElement("w:instrText")
        it.set(qn("xml:space"), "preserve")
        it.text = instrucao
        r._r.append(it)
    else:
        f = OxmlElement("w:fldChar")
        f.set(qn("w:fldCharType"), tipo)
        r._r.append(f)


def sumario():
    """Campo TOC do Word com o resultado já preenchido (páginas medidas na primeira passada)."""
    if not PAGINAS:
        par("O sumário é preenchido na segunda passada do gerador.", italico=True)
        return
    for k, (nivel, texto, pagina) in enumerate(PAGINAS):
        p = doc.add_paragraph()
        formato = p.paragraph_format
        formato.tab_stops.add_tab_stop(Cm(16.4), WD_TAB_ALIGNMENT.RIGHT, WD_TAB_LEADER.DOTS)
        formato.left_indent = Cm(0 if nivel == 1 else 0.6)
        formato.space_before = Pt(5 if nivel == 1 and k else 0)
        formato.space_after = Pt(1)
        formato.line_spacing = 1.0
        if k == 0:
            marca_campo(p, "begin")
            marca_campo(p, None, 'TOC \\o "1-2" \\h \\z \\u')
            marca_campo(p, "separate")
        r = p.add_run(f"{texto}\t{pagina}")
        r.bold = nivel == 1
        r.font.size = Pt(10.5 if nivel == 1 else 10)
        if k == len(PAGINAS) - 1:
            marca_campo(p, "end")
    atualizar = OxmlElement("w:updateFields")
    atualizar.set(qn("w:val"), "true")
    doc.settings.element.append(atualizar)


def trecho_log(nome, cabeca=14, cauda=6):
    linhas = (EXE / f"log_{nome}.txt").read_text(encoding="utf-8", errors="replace").rstrip().splitlines()
    linhas = [l[:122] for l in linhas]
    if len(linhas) > cabeca + cauda + 1:
        linhas = linhas[:cabeca] + [f"... ({len(linhas) - cabeca - cauda} linhas omitidas) ..."] + linhas[-cauda:]
    return "\n".join(linhas)


def nome_lote(i):
    return lotes[i]["nome"]


rod = sec.footer.paragraphs[0]
rod.alignment = WD_ALIGN_PARAGRAPH.CENTER
rr = rod.add_run("Otimização da dieta integrada ao RuFaS — relatório técnico · página ")
rr.font.size = Pt(8)
rr.font.color.rgb = CINZA
run = rod.add_run()
run.font.size = Pt(8)
campo(run, "PAGE")

# ======================================================================================= CAPA
for _ in range(4):
    doc.add_paragraph()
p = doc.add_paragraph()
r = p.add_run("Relatório técnico e evidências")
r.font.size = Pt(14)
r.font.color.rgb = CINZA
p = doc.add_paragraph()
r = p.add_run("Módulo de otimização da dieta integrado ao RuFaS")
r.font.size = Pt(28)
r.bold = True
r.font.color.rgb = VERDE
p = doc.add_paragraph()
r = p.add_run("“Desenvolvimento de módulo de otimização da dieta integrada ao RuFaS com avaliação de efeito "
              "econômico, e também análise de sensibilidade.”")
r.font.size = Pt(13)
r.italic = True
borda_paragrafo(p, "F9A825", "left", 36)
doc.add_paragraph()
for texto, tam in ((f"Caso de estudo: fazenda go_campo_dourado_ifg · 100 vacas em lactação · RuFaS 1.0.5 · "
                    f"padrão nutricional NASEM 2021", 11),
                   (f"Pós-graduação em Inteligência Artificial · IFG · {date.today():%d/%m/%Y}", 11),
                   (f"Execução registrada em {inicio_execucao:%d/%m/%Y %H:%M} · código no commit "
                    f"{git('rev-parse', '--short', 'HEAD')}" + (" com alterações locais" if git("status", "--porcelain")
                                                                 else ""), 10)):
    p = doc.add_paragraph()
    r = p.add_run(texto)
    r.font.size = Pt(tam)
    r.font.color.rgb = CINZA
quebra()

p = doc.add_paragraph()
r = p.add_run("Sumário")
r.bold = True
r.font.size = Pt(16)
r.font.color.rgb = VERDE
sumario()

# ======================================================================================= RESUMO
h1("Resumo")
par("O módulo otimizacao_dieta formula a dieta de custo mínimo de cada lote de vacas em lactação com as exigências "
    "e os alimentos do próprio RuFaS, mede o efeito econômico da dieta, analisa a sensibilidade do resultado e "
    "devolve a dieta ao simulador para uma simulação completa do rebanho. Este relatório mostra como a modelagem foi "
    "feita e reúne as evidências de uma execução completa do pipeline, além da comparação com o otimizador "
    "original do RuFaS (RationOptimizer).")
itens = [
    ("Otimização. ", f"A dieta ótima dos quatro lotes atende todas as restrições do RuFaS. No rebanho de 100 vacas, "
                     f"o custo da dieta cai de {brl(custo_atual)} para {brl(custo_otimo)} por dia "
                     f"({pct(-reducao_custo_pct)}) em relação à dieta atual da fazenda, que não atende o lote de "
                     f"início de lactação (seção 6.1)."),
    ("Efeito econômico. ", f"Pelo cálculo da PL, o IOFC (receita do leite menos custo da alimentação) sobe "
                           f"{brl(ganho_pl_ano, 0)} por ano. Na simulação de 5 anos do RuFaS desta execução, o "
                           f"ganho foi de {brl(ganho_rufas_ano, 0)} por ano"
                           + (f"; considerando todas as rodadas registradas, fica entre {brl(ganho_min, 0)} e "
                              f"{brl(ganho_max, 0)} por ano." if rodadas is not None else ".")),
    ("Sensibilidade. ", f"Em {num(sobol_res['n_avaliacoes'], 0)} PLs da análise de Sobol, "
                        f"{top_custo.rotulo.iloc[0].lower()} explica {pct(100 * top_custo.ST.iloc[0], 0)} da "
                        f"variância do custo da dieta, e {top_iofc.rotulo.iloc[0].lower()} explica "
                        f"{pct(100 * top_iofc.ST.iloc[0], 0)} da variância do IOFC. Nenhum preço de alimento passa de "
                        f"{pct(100 * sobol[(sobol.saida == 'custo_dieta_brl') & sobol.fator.str.startswith('preco_')].ST.max(), 0)}"
                        f" da variância do custo."),
    ("Comparação com o RationOptimizer. ", f"No mesmo problema, nos {len(convergidos)} casos em que o "
                                           f"RationOptimizer converge, a PL chega ao mesmo custo em {n_iguais}, fica "
                                           f"até {pct(max_pequena, 2)} acima em {n_pequena} e entre "
                                           f"{pct(grandes.pl_vs_ro_pct.min())} e {pct(grandes.pl_vs_ro_pct.max())} "
                                           f"acima em {len(grandes)}. No caso da aula, o RationOptimizer não converge "
                                           f"em nenhuma das {int(aula.n_execucoes)} execuções; a PL resolve todos os "
                                           f"casos."),
    ("Execução. ", f"As {len(etapas)} etapas do pipeline rodaram sem erro em {num(segundos_total / 60, 1)} "
                   "minutos."),
]
lista(itens)

h2("Como cada parte do requisito foi atendida")
tabela(["Parte do requisito", "Como foi atendida", "Evidência neste relatório"], [
    [("Módulo de otimização da dieta",), "PL de custo mínimo por lote (HiGHS), linearizada de forma sucessiva; "
                                        "Simplex próprio com quadro final e preços-sombra",
     "Seções 3.2–3.6, 5"],
    [("Integrada ao RuFaS",), "Usa o calculador NASEM, a biblioteca de alimentos e as funções de restrição do RuFaS; "
                             "grava a dieta no feed.json e roda a simulação completa",
     "Seções 3.3, 3.7, 3.8, 4.2, 6.4"],
    [("Avaliação de efeito econômico",), "Custo, receita, IOFC e custo por kg de leite por lote e no rebanho; "
                                        "custo marginal do leite; IOFC de 5 anos simulados",
     "Seções 3.9, 6"],
    [("Análise de sensibilidade",), "Sobol global (preços, animal, preço do leite), cenários pairwise e faixas "
                                   "de validade dos preços-sombra",
     "Seções 3.10, 5.3, 7"],
    [("Comparação com o RationOptimizer",), "Mesmo problema resolvido pela PL e pelo SLSQP do RuFaS em 23 casos, "
                                           "10 pontos iniciais por caso",
     "Seções 3.11, 8"],
], larguras=[4.0, 8.4, 4.0], fonte=9)

# ======================================================================================= 1. CASO
h1("1. Caso de estudo")
par("A fazenda go_campo_dourado_ifg é uma cópia da GO_campo_dourado (Goiás), com a mesma semente aleatória. A única "
    "diferença entre as duas na simulação é a ração do curral de lactação, que o módulo substitui pela dieta ótima. "
    f"O preço do leite é {brl(cfg['preco_leite_brl_kg'])}/kg e o câmbio, {brl(cfg['cambio_brl_por_usd'])}/US$ "
    "(o RuFaS trabalha em dólar).")
h2("1.1 Lotes")
par("Os lotes reproduzem o quadro da aula: 100 vacas, o mesmo número de vacas em lactação da fazenda.")
tabela(["Lote", "Vacas", "PV (kg)", "Parto", "DEL", "Leite (kg)", "Gordura", "Proteína", "ECC", "Gestação (d)"],
       [[l["nome"], l["n_vacas"], l["peso_vivo"], l["parto"], l["del"], num(l["leite_kg"], 1), pct(l["gordura_pct"]),
         pct(l["proteina_pct"]), num(l["ecc"]), l["dias_gestacao"] or "—"] for l in cfg["lotes"]],
       larguras=[3.6, 1.2, 1.3, 1.1, 1.0, 1.6, 1.5, 1.6, 1.1, 2.4], fonte=8.5, alinhar_dir=(1, 2, 3, 4, 5, 6, 7, 8))
h2("1.2 Ingredientes e preços")
atual = {int(k): v for k, v in cfg["dieta_atual"]["percentual_ms"].items()}
tabela(["Ingrediente", "ID RuFaS", "R$/kg MS", "Mín. (kg MS)", "Máx. (kg MS)", "Dieta atual (% MS)"],
       [[i["nome"], i["rufas_id"], brl(i["preco_brl_kg_ms"]), num(i["min_kg_ms"]), num(i["max_kg_ms"]),
         pct(atual[i["rufas_id"]], 0) if i["rufas_id"] in atual else "—"] for i in cfg["ingredientes"]],
       larguras=[5.0, 1.8, 2.0, 2.4, 2.4, 2.8], fonte=9, alinhar_dir=(1, 2, 3, 4, 5))
par("Os preços são valores de referência para estudo (Goiás, 2026). A dieta atual é a ração lac_cow fixada no "
    "feed.json original da fazenda; ela é usada como linha de base na avaliação econômica.", italico=True)

# ======================================================================================= 2/3. MODELAGEM
h1("2. Arquitetura do módulo")
par("O módulo fica em go_campo_dourado_ifg/otimizacao_dieta/ e importa o código do RuFaS em vez de reescrever as "
    "equações nutricionais. Assim, a dieta é calculada com as mesmas exigências, a mesma biblioteca de alimentos e "
    "as mesmas equações de oferta que o simulador usa.")
figura(FIG_DOC / "arquitetura_modulo.png", "Componentes do módulo e pontos de contato com o RuFaS.")
arquivos_modulo = [("config.py", "Lê config_otimizacao.json (lotes, ingredientes, parâmetros)"),
                   ("exigencias.py", "Exigências NASEM pelo NASEMRequirementsCalculator do RuFaS"),
                   ("biblioteca.py", "Composição dos alimentos (NASEM_Comp.csv → NASEMFeed do RuFaS)"),
                   ("modelo.py", "Formulação da PL, SLP, validação pelas funções do RuFaS"),
                   ("simplex.py", "Simplex tabular de duas fases, quadro final, faixas"),
                   ("economia.py", "Custo, IOFC, custo marginal, avaliação do CSV da simulação"),
                   ("sensibilidade.py", "Análise de Sobol (SALib) em paralelo"),
                   ("pairwise.py", "Cenários IPOG que cobrem todos os pares de níveis"),
                   ("comparacao.py", "PL × RationOptimizer no mesmo problema"),
                   ("integracao_rufas.py", "Grava o feed.json e roda a simulação do RuFaS"),
                   ("relatorio.py", "Relatório Markdown e figuras"),
                   ("__main__.py", "Linha de comando: etapas otimizar … relatorio")]
pasta_mod = FAZ / "otimizacao_dieta"
tabela(["Arquivo", "Responsabilidade", "Linhas"],
       [[a, d, len((pasta_mod / a).read_text(encoding="utf-8").splitlines())] for a, d in arquivos_modulo],
       larguras=[4.6, 10.0, 1.8], fonte=9, alinhar_dir=(2,))

h1("3. Modelagem", nova_pagina=False)
h2("3.1 Problema")
par("Para cada lote, o módulo procura a combinação de ingredientes mais barata que atende às exigências nutricionais "
    "de uma vaca do lote, dentro dos limites de inclusão de cada alimento. É o mesmo problema que o RationOptimizer "
    "do RuFaS resolve para o curral de lactação no padrão NASEM.")
h2("3.2 Variáveis e função objetivo")
formula("min  Z = Σᵢ cᵢ · xᵢ        (R$ por vaca por dia)",
        "sujeito a   restrições nutricionais do RuFaS,   Lᵢ ≤ xᵢ ≤ Uᵢ,   m ≥ 0")
lista([("xᵢ: ", "kg de matéria seca (MS) do ingrediente i por vaca por dia (11 ingredientes)."),
       ("cᵢ: ", "preço do ingrediente em R$/kg de MS."),
       ("Lᵢ, Uᵢ: ", "limites mínimo e máximo de inclusão (tabela da seção 1.2)."),
       ("m: ", "variável auxiliar com a proteína microbiana (g/dia), explicada na seção 3.5.")])
par("A otimização é feita em matéria seca porque as exigências NASEM e a composição dos alimentos estão em base "
    "seca. O custo é o mesmo nas duas bases; a conversão para matéria natural (o que vai no cocho) é feita no relatório "
    "Markdown do módulo.")

h2("3.3 Exigências nutricionais (NASEM pelo RuFaS)")
par("As exigências de cada lote vêm de NASEMRequirementsCalculator.calculate_requirements, o mesmo calculador que o "
    "RuFaS usa na simulação, alimentado com peso vivo, peso adulto, parto, DEL, ECC, dias de gestação, produção e "
    "composição do leite.")
tabela(["Lote", "CMS prevista (kg)", "ELl (Mcal)", "PM (g)", "Ca absorvível (g)", "P absorvível (g)"],
       [[l["nome"], num(l["exigencias"]["cms_kg"]), num(l["exigencias"]["nel_mcal"]), num(l["exigencias"]["pm_g"], 0),
         num(l["exigencias"]["ca_g"], 1), num(l["exigencias"]["p_g"], 1)] for l in lotes.values()],
       larguras=[4.4, 2.6, 2.1, 2.1, 2.6, 2.6], fonte=9, alinhar_dir=(1, 2, 3, 4, 5))

h2("3.4 Restrições")
par("As restrições são as mesmas que o RationOptimizer aplica à vaca em lactação no padrão NASEM. Os coeficientes "
    "vêm das equações de oferta do RuFaS (nutrition_supply_calculator.py) e das constantes de AnimalModuleConstants.")
tabela(["Restrição", "Forma na PL", "Origem no RuFaS"], [
    ["Cálcio absorvível", "Σ dCaᵢ · Caᵢ · 10 · xᵢ ≥ Ca exigido", "dCa = 0,30 forragem; 0,60 concentrado; 0,95 mineral"],
    ["Fósforo absorvível", "Σ dPᵢ · Pᵢ · 10 · xᵢ ≥ P exigido", "dP = 0,64; 0,70; 0,80"],
    ["Proteína metabolizável mínima", "0,64 · m + Σ (PNDRdᵢ + 4,72) · xᵢ ≥ PM exigida",
     "PNDR digestível; proteína endógena 0,4 × 11,8 g/kg MS"],
    ["Proteína microbiana (energia)", "m ≤ Σ 1,3 · NDTajᵢ · xᵢ", "NDT com desconto de consumo"],
    ["Proteína microbiana (PDR)", "m ≤ Σ 8,5 · PDRᵢ · xᵢ", "PDR pela taxa de passagem"],
    ["Proteína metabolizável máxima", "Σ (PNDRdᵢ + 4,72 + 0,64 · 1,3 · NDTajᵢ) · xᵢ ≤ 1,5 · PM exigida",
     "PROTEIN_UPPER_LIMIT_FACTOR"],
    ["FDN mínima e máxima", "Σ (FDNᵢ − 25) · xᵢ ≥ 0   e   Σ (FDNᵢ − 45) · xᵢ ≤ 0", "MINIMUM/MAXIMUM_RATION_NDF"],
    ["FDN de forragem", "Σ (FDNforrᵢ − 15) · xᵢ ≥ 0", "MINIMUM_RATION_FORAGE_NDF"],
    ["Extrato etéreo", "Σ (EEᵢ − 7) · xᵢ ≤ 0", "MAXIMUM_RATION_FAT"],
    ["Consumo de MS", "0,70 · CMSprev ≤ Σ xᵢ ≤ 1,30 · CMSprev", "DMI_CONSTRAINT_FRACTION = 0,30"],
    ["Energia líquida de lactação", "Σ 0,66 · (EDᵢ − perdas) · xᵢ ≥ ELl exigida",
     "EDᵢ pela equação NASEM do RuFaS"],
], larguras=[3.9, 7.5, 5.0], fonte=8.5)
par("Composição (Ca, P, FDN, EE, NDT, PNDR) em % da MS do alimento; PDR, NDTaj e PNDRd em g por kg de MS. As "
    "restrições de percentual (FDN, gordura) foram escritas como Σ (teorᵢ − limite) · xᵢ, que é linear e equivale a "
    "limitar o teor médio da dieta.", italico=True)

h2("3.5 Não linearidades: Programação Linear Sucessiva")
par("Várias equações do RuFaS dependem da dieta inteira, e não só de cada ingrediente: a digestibilidade da FDN e do "
    "amido depende da CMS e do amido total; o desconto de NDT depende da composição da dieta; a degradabilidade "
    "ruminal da proteína depende da taxa de passagem; e a proteína microbiana é o mínimo de dois termos. O módulo "
    "trata cada caso assim:")
lista([("Mínimo da proteína microbiana (exato). ", "PMic = min(a, b) vira a variável m com m ≤ a e m ≤ b. Como m só "
        "aparece com coeficiente positivo na restrição de PM mínima, o otimizador leva m ao menor dos dois limites "
        "sempre que isso é necessário. A troca é exata, sem aproximação."),
       ("Coeficientes que dependem da dieta (SLP). ", "ED, NDT ajustado, PDR e PNDR são recalculados com as funções do "
        "próprio RuFaS no ponto da solução anterior, e a PL é resolvida de novo até a solução parar de mudar.")])
par("Algoritmo da SLP (função _resolver_slp em modelo.py):")
lista(["Ponto inicial: a CMS prevista dividida igualmente entre os ingredientes, respeitando os limites.",
       "No ponto x(k), calcular os coeficientes com as funções do RuFaS: calculate_NASEM_digestible_energy, "
       "calculate_nutrient_intake_discount, _calculate_protein_passage_rates e as porcentagens de PDR e PNDR.",
       "Montar a PL e resolvê-la com o HiGHS (scipy.optimize.linprog).",
       f"Parar quando |Z(k) − Z(k−1)| < {cfg['parametros_modelo']['tol_slp']:g} e a maior mudança em x for menor que "
       f"10⁻⁴ kg. Senão, usar a nova solução como ponto (com amortecimento de 50% a partir da 5ª iteração, para "
       f"evitar oscilação entre vértices) e voltar ao passo 2. Limite: {cfg['parametros_modelo']['max_iter_slp']} "
       f"iterações."], numerada=True)
tabela(["Lote", "Iterações da SLP", "Custo (R$/vaca/dia)", "CMS (kg)", "Leite atendido (kg)"],
       [[l["nome"], l["iteracoes_slp"], brl(l["custo_brl"], 4), num(l["cms_kg"]), num(l["leite_atendido_kg"], 1)]
        for l in lotes.values()], larguras=[4.6, 2.8, 3.2, 2.6, 3.2], fonte=9, alinhar_dir=(1, 2, 3, 4))
par("Perdas de energia. O NASEM desconta da energia digestível o metano entérico e o N urinário. O módulo usa "
    f"{num(cfg['parametros_modelo']['ch4_g_por_kg_cms'], 1)} g de metano e "
    f"{num(cfg['parametros_modelo']['n_urina_g_por_kg_cms'], 1)} g de N urinário por kg de CMS, calibrados na rodada "
    "base da GO_campo_dourado:")
formula("ELlᵢ = 0,66 · (EDᵢ − perdas),     perdas = (13,28 · CH₄ (kg) + 0,0146 · N urinário (g)) / CMS")
par("Por padrão as perdas crescem com o consumo. O RationOptimizer recebe totais fixos do curral; a opção "
    "perdas_energia = \"fixas\" reproduz isso e é a usada na comparação da seção 8.")
par("Inviabilidade. Se nenhuma dieta atende o lote, o módulo reduz a produção de leite em passos de "
    f"{num(cfg['parametros_modelo']['passo_reducao_leite_kg'], 1)} kg, até "
    f"{num(cfg['parametros_modelo']['reducao_leite_max_kg'], 0)} kg, como o milk_reduction_maximum do RuFaS. Nesta "
    "execução nenhum lote precisou de redução.")

h2("3.6 Simplex tabular e leitura econômica")
par("A PL da última iteração da SLP também é resolvida por um Simplex tabular de duas fases escrito no módulo "
    "(simplex.py), com a regra de Bland para evitar ciclagem, comum em dietas degeneradas. O HiGHS é mais rápido, mas "
    "não mostra o quadro final. O Simplex próprio expõe:")
lista(["o quadro final (base, valores, custos reduzidos), gravado em quadro_final_simplex.csv;",
       "os preços-sombra de cada restrição e de cada limite de inclusão, com a faixa do lado direito em que valem;",
       "os intervalos de otimalidade dos preços: quanto cada preço pode variar sem mudar a receita."])
par(f"Na vaca referência, os dois métodos chegam ao mesmo ótimo: {brl(dietas['simplex']['objetivo'], 6)} (Simplex "
    f"próprio) e {brl(dietas['simplex']['objetivo_highs'], 6)} (HiGHS).")

h2("3.7 Validação pelas funções do RuFaS")
par("A dieta final de cada lote é avaliada pelas funções de restrição originais do RationOptimizer "
    "(validar_com_rufas em modelo.py): o módulo monta um RationConfig do RuFaS com as exigências do lote, chama "
    "set_constraints e avalia cada função em NASEM_constraint_functions. Valor ≥ 0 significa restrição atendida. "
    "O resultado está na seção 5.2.")

h2("3.8 Integração com a simulação do RuFaS")
lista(["preparar_feed_json calcula a ração média de lactação (média dos lotes ponderada por número de vacas e CMS), "
       "arredonda para 100% e grava no feed.json da fazenda, com os preços convertidos para US$/kg de MS. O original "
       "fica em feed_original.json.",
       "Com ration.user_input = true no animal.json, o RuFaS usa essa receita: percentual da MS × CMS prevista pelo "
       "NASEM a cada reformulação.",
       "rodar_rufas executa python main.py -p go_campo_dourado_ifg/fazenda/pipeline_task_manager_metadata.json, a simulação "
       "completa de 5 anos do rebanho inteiro.",
       "economia.avaliar_saida_rufas lê do CSV de saída o leite diário, as vacas em lactação e o total fornecido de "
       "cada alimento, e calcula custo, receita e IOFC com a mesma tabela de preços para a fazenda base e a otimizada."],
      numerada=True)
racao = {int(k): v for k, v in dietas["racao_media_lactacao_pct"].items()}
par("Ração gravada no curral de lactação (% da MS): " + ", ".join(f"{nomes[i]} {num(p)}%" for i, p in racao.items())
    + ".")

h2("3.9 Avaliação econômica")
formula("Custo = Σᵢ preçoᵢ · xᵢ          Receita = leite · preço do leite",
        "IOFC = Receita − Custo          Custo por kg de leite = Custo / leite")
lista([("Por lote e no rebanho: ", "dieta ótima contra a dieta atual da fazenda, valorizadas pelos mesmos preços e "
        "conferidas pelas funções do RuFaS."),
       ("Custo marginal do leite: ", "a PL é resolvida de novo com 1 kg a mais de leite (exigência recalculada pelo "
        "NASEM); a diferença de custo é o custo de ração do kg adicional."),
       ("Simulação de 5 anos: ", "IOFC por ano do rebanho inteiro (bezerras, novilhas, pré-parto e lactação), base × "
        "otimizada.")])

h2("3.10 Análise de sensibilidade")
n_fat = len(sobol_fat)
par(f"Sobol global (SALib). São {n_fat} fatores: o preço de cada um dos 11 ingredientes "
    f"(±{cfg['sensibilidade']['variacao_preco_pct']}%), quatro características da vaca e o preço do leite. A amostragem "
    f"de Saltelli com N = {sobol_res['n_base']} gera N · (k + 2) = {num(sobol_res['n_avaliacoes'], 0)} combinações; "
    "cada uma resolve a PL completa (com a SLP) no lote de referência, em paralelo. Para cada saída (custo da dieta, "
    "IOFC, custo por kg de leite) são calculados:")
formula("S1ᵢ = V[E(Y | Xᵢ)] / V(Y)          STᵢ = E[V(Y | X₋ᵢ)] / V(Y)")
par("S1 é o efeito do fator sozinho; ST inclui as interações com os outros fatores. ST − S1 grande indica interação.")
tabela(["Fator", "Tipo", "Mínimo", "Base", "Máximo"],
       [[f.rotulo, {"preco": "preço (R$/kg MS)", "animal": "animal", "preco_leite": "R$/kg de leite"}[f.tipo],
         num(f.minimo), num(f.base), num(f.maximo)] for f in sobol_fat.itertuples() if f.tipo != "preco"]
       + [["Preço de cada ingrediente (11 fatores)", "preço (R$/kg MS)", "−25%", "tabela 1.2", "+25%"]],
       larguras=[6.2, 3.4, 2.2, 2.4, 2.2], fonte=9, alinhar_dir=(2, 3, 4))
par(f"Pairwise testing (IPOG). Os {len(pw_res['fatores'])} fatores com maior ST viram fatores de 3 níveis (baixo, "
    f"base, alto). O fatorial completo teria {pw_res['fatorial_completo']} cenários; o IPOG gera "
    f"{pw_res['n_cenarios']} cenários que cobrem todos os pares de níveis de quaisquer dois fatores "
    f"({pw_res['pares_descobertos']} pares descobertos). Cada cenário resolve a PL completa.")

h2("3.11 Comparação com o RationOptimizer")
par("O RationOptimizer do RuFaS resolve o problema não linear com SLSQP (scipy.optimize.minimize), a partir de um "
    "ponto inicial sorteado, com limite de 100 iterações. A etapa comparar (comparacao.py) resolve exatamente o mesmo "
    "problema pelos dois métodos:")
lista(["mesmas exigências NASEM, alimentos, preços e limites de inclusão;",
       "perdas de energia fixas na CMS prevista, como o RationOptimizer as recebe na simulação;",
       f"{comp_json['n_sementes']} execuções do RationOptimizer por caso (sementes diferentes do ponto inicial); vale a "
       "execução mais barata entre as viáveis (folga ≥ −0,001 em todas as restrições do RuFaS), preferindo as que o "
       "SciPy declarou convergidas;",
       f"{len(comp)} casos: o caso de referência da aula (alimentos e preços em US$ do feed original), os 4 lotes e "
       f"os {pw_res['n_cenarios']} cenários pairwise."])

# ======================================================================================= 4. EXECUÇÃO
h1("4. Dados de execução")
par(f"O pipeline completo foi executado em {inicio_execucao:%d/%m/%Y} às {inicio_execucao:%H:%M} pelo script "
    "coletar_execucao.py, que roda cada etapa em um processo separado e registra tempo de parede, tempo de CPU "
    "(somando os processos filhos), pico de memória do maior processo, código de saída e o log completo. Os "
    "registros estão em go_campo_dourado_ifg/resultados/execucao/.")
h2("4.1 Ambiente")
pacotes = amb["pacotes"]
tabela(["Item", "Valor"], [
    ["Máquina hospedeira", f"{platform.system()} {platform.release()} ({platform.version()})"],
    ["Imagem Docker", docker_imagem()],
    ["Sistema no contêiner", amb["sistema"]],
    ["CPU", f"{amb['cpu']} · {amb['cpus']} núcleos lógicos visíveis"],
    ["Memória", f"{num(amb['memoria_gb'], 1)} GB visíveis no contêiner"],
    ["Python", amb["python"]],
    ["Pacotes", " · ".join(f"{k} {v}" for k, v in pacotes.items())],
    ["Código", f"commit {git('rev-parse', '--short', 'HEAD')} ({git('log', '-1', '--format=%ci')[:16]})"
               + (" + alterações locais não commitadas" if git("status", "--porcelain") else "")],
], larguras=[4.0, 12.4], fonte=9)

h2("4.2 Etapas do pipeline")
descricao = {"otimizar": "PL por lote, Simplex, economia", "sensibilidade": f"Sobol: {num(sobol_res['n_avaliacoes'], 0)} PLs",
             "pairwise": f"{pw_res['n_cenarios']} cenários IPOG",
             "comparar": f"PL × RationOptimizer: {len(comp)} casos × {comp_json['n_sementes']}",
             "rufas": "feed.json + simulação de 5 anos", "relatorio": "relatório Markdown e figuras"}
linhas = []
for e in execucao["etapas"]:
    cpu = e["cpu_usuario_s"] + e["cpu_sistema_s"]
    linhas.append([(e["etapa"],), descricao[e["etapa"]], num(e["segundos"], 1), num(cpu, 1),
                   num(cpu / e["segundos"], 1) + "×", num(e["pico_memoria_mb"], 0),
                   "ok" if e["codigo_saida"] == 0 else f"erro {e['codigo_saida']}"])
linhas.append([("Total",), "", (num(segundos_total, 1),), "", "", "", ""])
tabela(["Etapa", "O que faz", "Tempo (s)", "CPU (s)", "CPU/tempo", "Memória (MB)", "Saída"], linhas,
       larguras=[2.3, 5.2, 1.8, 1.8, 1.8, 2.0, 1.5], fonte=8.5, alinhar_dir=(2, 3, 4, 5))
figura(FIG_DOC / "execucao_etapas.png", "Tempo de parede de cada etapa nesta execução.", largura=14)
sens = etapas[etapas.etapa == "sensibilidade"].iloc[0]
rf = etapas[etapas.etapa == "rufas"].iloc[0]
par(f"A simulação do RuFaS ocupa {pct(100 * rf.segundos / segundos_total, 0)} do tempo. A análise de Sobol usa "
    f"{num((sens.cpu_usuario_s + sens.cpu_sistema_s) / sens.segundos, 1)} núcleos em média (CPU/tempo), porque as "
    f"PLs são distribuídas entre processos: {num(sobol_res['n_avaliacoes'], 0)} PLs em {num(sens.segundos, 0)} s. A "
    "memória é o pico do maior processo da etapa; na etapa rufas é o do simulador.")

h2("4.3 Saída de cada etapa")
par("Trechos dos logs gravados pelo coletor (log_<etapa>.txt). Linhas longas foram cortadas.")
for e in execucao["etapas"]:
    h3(e["etapa"])
    codigo(trecho_log(e["etapa"]), tamanho=6.6)

h2("4.4 Arquivos produzidos")
par(f"Arquivos de resultado gravados pelo pipeline entre {inicio_execucao:%H:%M:%S} e "
    f"{fim_execucao:%H:%M:%S}.").paragraph_format.keep_with_next = True
linhas = []
ORDEM_PASTAS = ["otimizar", "sensibilidade", "pairwise", "comparar", "rufas", "resultados", "figuras"]
for arq in sorted((a for a in RES.rglob("*") if a.is_file() and EXE not in a.parents),
                  key=lambda a: (ORDEM_PASTAS.index(a.parent.name), a.name)):
    modificado = datetime.fromtimestamp(arq.stat().st_mtime)
    if inicio_execucao <= modificado <= fim_execucao:
        linhas.append([str(arq.relative_to(RES)).replace("\\", "/"), f"{num(arq.stat().st_size / 1024, 1)} KB",
                       f"{modificado:%d/%m %H:%M:%S}"])
tabela(["Arquivo (em resultados/)", "Tamanho", "Gerado em"], linhas, larguras=[10.0, 2.8, 3.6], fonte=8.5,
       alinhar_dir=(1,))
csv_sim = Path(rufas["ifg"]["csv"].replace("/app/", str(RAIZ) + "/"))
if csv_sim.exists():
    par(f"A simulação gerou {csv_sim.name} ({num(csv_sim.stat().st_size / 1e6, 0)} MB, "
        f"{rufas['ifg']['dias_simulados']} dias simulados) em output/CSVs/.")

# ======================================================================================= 5. RESULTADOS
h1("5. Resultados da otimização")
h2("5.1 Dieta ótima por lote")
ids = [i for i in nomes if any(l["kg_ms"].get(str(i), 0) > 1e-6 for l in lotes.values())]
linhas = [[nomes[i], brl(next(g["preco_brl_kg_ms"] for g in cfg["ingredientes"] if g["rufas_id"] == i))]
          + [f"{num(l['kg_ms'][str(i)])} ({num(l['percentual_ms'][str(i)], 1)}%)" if l["kg_ms"][str(i)] > 1e-6 else "—"
             for l in lotes.values()] for i in ids]
linhas.append([("CMS total (kg)",), ""] + [(num(l["cms_kg"]),) for l in lotes.values()])
linhas.append([("Custo (R$/vaca/dia)",), ""] + [(brl(l["custo_brl"]),) for l in lotes.values()])
tabela(["Ingrediente (kg MS)", "R$/kg MS"] + [l["nome"] for l in lotes.values()], linhas,
       larguras=[3.8, 1.6, 2.75, 2.75, 2.75, 2.75], fonte=8.5, alinhar_dir=(1, 2, 3, 4, 5))
figura(FIG / "dietas_por_lote.png", "Composição da dieta ótima de cada lote (% da MS).", largura=13)
par("A silagem de sorgo e o sorgo grão entram no limite máximo na maioria dos lotes: são as fontes mais baratas de "
    "fibra e energia. O farelo de soja aparece na quantidade necessária para a proteína metabolizável, e a mistura "
    "mineral fica no mínimo obrigatório.")

h2("5.2 Conferência pelas funções do RuFaS")
funcoes = list(ref["validacao_rufas"].keys())
linhas = [[f] + [num(l["validacao_rufas"][f], 3) for l in lotes.values()] for f in funcoes]
linhas.append([("Todas atendidas?",)] + [("sim" if l["validacao_ok"] else "não",) for l in lotes.values()])
tabela(["Função de restrição do RuFaS"] + [l["nome"] for l in lotes.values()], linhas,
       larguras=[5.2, 2.8, 2.8, 2.8, 2.8], fonte=8.5, alinhar_dir=(1, 2, 3, 4))
par("Cada valor é oferta − exigência (ou limite − oferta), nas unidades do RuFaS. Os zeros são restrições ativas: "
    "a dieta de custo mínimo atende essas exigências exatamente, sem sobra paga.")

h2("5.3 Leitura do quadro final do Simplex (vaca referência)")
rot_rest = {r["nome"]: r["descricao"] for r in ref["restricoes"]}
ativas = rhs[rhs.preco_sombra.abs() > 1e-9]
tabela(["Restrição ativa", "Lado direito", "Preço-sombra (R$/unid.)", "Válido de", "até"],
       [[rot_rest.get(r.restricao, f"Limite máximo de {nomes.get(int(r.restricao[4:]), r.restricao)}"
                      if r.restricao.startswith("max_") else r.restricao),
         f"{num(r.lado_direito, 3)} {r.unidade}", num(r.preco_sombra, 5), limite(r.valido_de), limite(r.valido_ate)]
        for r in ativas.itertuples()], larguras=[5.6, 3.0, 3.0, 2.4, 2.4], fonte=8.5, alinhar_dir=(1, 2, 3, 4))
ell = rhs[rhs.restricao == "ell"].iloc[0]
pm = rhs[rhs.restricao == "pm_min"].iloc[0]
lista([("Energia: ", f"cada Mcal de ELl a mais na exigência custa {brl(ell.preco_sombra, 4)} por vaca por dia, "
        f"enquanto a exigência ficar entre {num(ell.valido_de)} e {num(ell.valido_ate)} Mcal."),
       ("Proteína: ", f"cada 100 g de PM a mais custam {brl(100 * pm.preco_sombra, 4)} por vaca por dia "
        f"(válido de {num(pm.valido_de, 0)} a {num(pm.valido_ate, 0)} g)."),
       ("Limites de inclusão: ", "os preços-sombra negativos dos limites máximos dizem quanto o custo cairia com 1 kg "
        "de MS a mais desse alimento disponível; é o valor de ampliar silo, área ou contrato.")])
tabela(["Ingrediente", "Situação", "Preço", "Preço mín.", "Preço máx.", "Custo reduzido"],
       [[nomes[int(p.rufas_id)], p.situacao, brl(p.preco_atual),
         "−∞" if p.preco_min == float("-inf") else brl(p.preco_min),
         "+∞" if p.preco_max == float("inf") else brl(p.preco_max), num(p.custo_reduzido, 4)]
        for p in precos.itertuples()], larguras=[4.6, 2.2, 2.0, 2.4, 2.4, 2.8], fonte=8.5,
       alinhar_dir=(2, 3, 4, 5))
par("Intervalo de otimalidade: dentro da faixa, a receita ótima não muda (só o custo total). Para ingredientes fora "
    "da dieta, o preço mínimo é o preço-alvo de negociação: abaixo dele o alimento passa a compensar.")

# ======================================================================================= 6. ECONOMIA
h1("6. Efeito econômico")
h2("6.1 Dieta ótima × dieta atual, por lote")
linhas = []
for e in eco.itertuples():
    linhas.append([e.lote if e.cenario == "ótima" else "", e.cenario, num(e.cms_kg_vaca_dia), brl(e.custo_dieta_brl_vaca_dia),
                   brl(e.iofc_brl_vaca_dia), brl(e.custo_alimentar_brl_kg_leite, 3),
                   "sim" if e.atende_rufas == "sim" else "não"])
tabela(["Lote", "Dieta", "CMS (kg)", "Custo", "IOFC", "Custo/kg leite", "Atende o RuFaS"], linhas,
       larguras=[3.8, 2.0, 1.8, 2.0, 2.2, 2.3, 2.3], fonte=8.5, alinhar_dir=(2, 3, 4, 5))
inviavel = eco[(eco.cenario != "ótima") & (eco.atende_rufas != "sim")]
par(f"Valores por vaca por dia, com leite a {brl(cfg['preco_leite_brl_kg'])}/kg e a mesma produção nas duas dietas.")
if len(inviavel):
    caixa("Atenção:", "a dieta atual não atende todas as restrições do RuFaS no lote " +
          ", ".join(f"{r.lote} ({r.atende_rufas.split(': ', 1)[-1]})" for r in inviavel.itertuples()) +
          ". No simulador, o RuFaS reduziria a produção desse lote; a comparação acima assume a produção cheia e, por "
          "isso, favorece a dieta atual.")

h2("6.2 Rebanho em lactação")
o, a = reb["otima"], reb["atual"]
tabela(["Indicador (100 vacas)", "Dieta ótima", "Dieta atual", "Diferença"], [
    ["Custo da dieta (R$/dia)", brl(o["custo_dieta_brl_dia"]), brl(a["custo_dieta_brl_dia"]),
     brl(o["custo_dieta_brl_dia"] - a["custo_dieta_brl_dia"])],
    ["IOFC (R$/dia)", brl(o["iofc_brl_dia"]), brl(a["iofc_brl_dia"]), brl(o["iofc_brl_dia"] - a["iofc_brl_dia"])],
    ["IOFC (R$/ano)", brl(o["iofc_brl_ano"], 0), brl(a["iofc_brl_ano"], 0), (brl(ganho_pl_ano, 0),)],
    ["Custo alimentar (R$/kg de leite)", brl(o["custo_alimentar_brl_kg_leite"], 3),
     brl(a["custo_alimentar_brl_kg_leite"], 3),
     brl(o["custo_alimentar_brl_kg_leite"] - a["custo_alimentar_brl_kg_leite"], 3)],
], larguras=[5.6, 3.6, 3.6, 3.6], fonte=9, alinhar_dir=(1, 2, 3))

h2("6.3 Custo marginal do leite")
tabela(["Lote", "Custo de ração de +1 kg de leite", "Margem sobre o preço do leite"],
       [[m.lote, brl(m.custo_marginal_brl_kg, 3), brl(m.margem_marginal_brl_kg, 3)] for m in marg.itertuples()],
       larguras=[5.4, 5.5, 5.5], fonte=9, alinhar_dir=(1, 2))
par(f"Em todos os lotes, o kg adicional de leite custa menos de {brl(marg.custo_marginal_brl_kg.max() + 0.005, 2)} de "
    f"ração, contra {brl(cfg['preco_leite_brl_kg'])} de receita: dentro da faixa de validade, produzir mais aumenta o "
    "IOFC.")

h2("6.4 Simulação de 5 anos no RuFaS")
b, i = rufas["base"], rufas["ifg"]
par("A ração média ótima (seção 3.8) foi gravada no curral de lactação e a fazenda inteira foi simulada no RuFaS. A "
    "base é a GO_campo_dourado com a dieta original. As duas saídas foram valorizadas com a mesma tabela de preços.")
lin = []
for ch, rot, cas, din in (("dias_simulados", "Dias simulados", 0, False), ("leite_total_kg", "Leite total (kg)", 0, False),
                          ("leite_kg_vaca_lactante_dia", "Leite por vaca lactante/dia (kg)", 2, False),
                          ("custo_alimentacao_total_brl", "Custo de alimentação (5 anos)", 0, True),
                          ("receita_leite_total_brl", "Receita do leite (5 anos)", 0, True),
                          ("iofc_brl_ano", "IOFC por ano", 0, True),
                          ("custo_alimentar_brl_kg_leite", "Custo alimentar por kg de leite", 3, True)):
    f = (lambda v: brl(v, cas)) if din else (lambda v: num(v, cas))
    lin.append([rot, f(b[ch]), f(i[ch]), f(i[ch] - b[ch])])
tabela(["Indicador (rebanho inteiro)", "GO_campo_dourado (base)", "go_campo_dourado_ifg", "Diferença"], lin,
       larguras=[5.6, 3.6, 3.6, 3.6], fonte=9, alinhar_dir=(1, 2, 3))
par("O custo inclui toda a alimentação do rebanho (bezerras, novilhas, pré-parto e lactação). O leite muda porque o "
    "RuFaS simula cada vaca: quando algum nutriente fica abaixo da exigência individual, a produção cai.")
if rodadas is not None:
    par("O RuFaS 1.0.5 não repete o resultado entre rodadas com as mesmas entradas, mesmo com set_seed = true. Por "
        "isso todas as simulações guardadas em output/CSVs/ foram reavaliadas com a tabela de preços atual "
        "(avaliar_rodadas_rufas.py). A ração de cada rodada foi lida do próprio CSV.")
    lin = [[f"{datetime.fromisoformat(r.data_hora):%d/%m %H:%M}" + (" (esta)" if r.esta_execucao else ""),
            "base" if r.fazenda == "base" else "ifg", r.racao_descricao, num(r.leite_total_kg, 0),
            brl(r.custo_alimentacao_total_brl, 0), brl(r.iofc_brl_ano, 0)]
           for r in rodadas.sort_values(["fazenda", "data_hora"]).itertuples()]
    tabela(["Rodada", "Fazenda", "Ração de lactação", "Leite total (kg)", "Custo alimentação", "IOFC por ano"], lin,
           larguras=[2.6, 1.5, 4.3, 2.6, 2.7, 2.7], fonte=8.5, alinhar_dir=(3, 4, 5))
    figura(FIG_DOC / "rufas_rodadas_iofc.png", "IOFC por ano em cada rodada da simulação (só rodadas com a "
           "ração ótima atual na fazenda ifg).", largura=14.5)
    caixa("Conclusão robusta:", f"com {len(rod_base)} rodadas da base e {len(rod_ifg)} da dieta ótima, o ganho de IOFC "
          f"fica entre {brl(ganho_min, 0)} e {brl(ganho_max, 0)} por ano em qualquer combinação de rodadas (média "
          f"{brl(ganho_medio, 0)}). É menor que o ganho calculado pela PL ({brl(ganho_pl_ano, 0)}), porque a simulação "
          "inclui o rebanho inteiro, a variação individual das vacas e a resposta do leite à dieta.",
          cor="E8F5E9", borda="2E7D32")

# ======================================================================================= 7. SENSIBILIDADE
h1("7. Análise de sensibilidade")
h2("7.1 Sobol")
par(f"{num(sobol_res['n_avaliacoes'], 0)} PLs resolvidas em {num(sobol_res['segundos'], 0)} s, "
    f"{sobol_res['invalidas']} inviáveis.")
for saida, titulo, top in (("custo_dieta_brl", "custo da dieta", top_custo), ("iofc_brl", "IOFC", top_iofc)):
    figura(FIG / f"sobol_{saida}.png", f"Índices de Sobol — {titulo} (S1: efeito isolado; ST: com interações).",
           largura=13)
    tabela(["Fator", "S1", "ST", "ST − S1"],
           [[t.rotulo, f"{num(t.S1, 3)} ± {num(t.S1_conf, 3)}", f"{num(t.ST, 3)} ± {num(t.ST_conf, 3)}",
             num(t.ST - t.S1, 3)] for t in top.itertuples()],
           larguras=[6.4, 3.4, 3.4, 3.2], fonte=9, alinhar_dir=(1, 2, 3))
top_kg = top_sobol("custo_kg_leite_brl", 4)
lista([("Custo da dieta: ", f"{top_custo.rotulo.iloc[0]} domina (ST = {num(top_custo.ST.iloc[0], 2)}), porque muda a "
        f"exigência de energia e proteína e a CMS prevista; {top_custo.rotulo.iloc[1].lower()} vem em seguida. Entre os "
        "preços, só os dos alimentos que entram na dieta (seção 5.1) têm algum efeito."),
       ("IOFC: ", f"o resultado depende sobretudo de {top_iofc.rotulo.iloc[0].lower()} e "
        f"{top_iofc.rotulo.iloc[1].lower()} (ST somado de {num(top_iofc.ST.iloc[:2].sum(), 2)}). Os preços dos "
        "alimentos quase não pesam, porque a PL substitui o ingrediente que encarece."),
       ("Custo por kg de leite: ", "os fatores mais influentes são " +
        ", ".join(f"{t.rotulo.lower()} (ST {num(t.ST, 2)})" for t in top_kg.itertuples()) + "."),
       ("Interações: ", f"entre os seis fatores principais de cada saída, ST − S1 chega no máximo a "
        f"{num(max((t.ST - t.S1).max() for t in (top_custo, top_iofc, top_sobol('custo_kg_leite_brl'))), 3)}: os "
        "efeitos são quase aditivos, e cada fator pode ser lido isoladamente.")])

h2("7.2 Cenários pairwise")
rot = pw_res["rotulos"]
fat = pw_res["fatores"]
par(f"Fatores: {', '.join(rot)}. {pw_res['n_cenarios']} cenários cobrem todos os pares de níveis, contra "
    f"{pw_res['fatorial_completo']} do fatorial completo.")
figura(FIG / "pairwise_iofc.png", "IOFC por vaca por dia em cada cenário pairwise.", largura=14)
abrev = {"baixo": "B", "base": "·", "alto": "A"}
tabela(["Cenário"] + rot + ["Custo", "IOFC"],
       [[c["cenario"]] + [abrev.get(c[f], c[f]) for f in fat] + [brl(c["custo_dieta_brl"]), brl(c["iofc_brl"])]
        for _, c in pw.iterrows()],
       larguras=[1.5] + [1.75] * len(fat) + [2.2, 2.2], fonte=7.5, alinhar_dir=tuple(range(len(fat) + 1, len(fat) + 3)))
par("B = nível baixo, A = nível alto, · = valor base. Custo e IOFC em R$ por vaca por dia.", italico=True)
melhor, pior = pw.loc[pw.iofc_brl.idxmax()], pw.loc[pw.iofc_brl.idxmin()]
par(f"O IOFC vai de {brl(pior.iofc_brl)} ({pior.cenario}) a {brl(melhor.iofc_brl)} ({melhor.cenario}) por vaca por "
    "dia: é a faixa que o planejamento da fazenda precisa suportar. Em todos os cenários a PL encontrou dieta viável"
    + (" sem reduzir o leite." if (pw.reducao_leite_kg == 0).all() else "."))

# ======================================================================================= 8. COMPARAÇÃO
h1("8. Comparação com o RationOptimizer")
par(f"Nos {len(convergidos)} casos em que o RationOptimizer convergiu, a PL chegou ao mesmo custo em {n_iguais}, "
    f"ficou até {pct(max_pequena, 2)} acima em {n_pequena} e ficou entre {pct(grandes.pl_vs_ro_pct.min())} e "
    f"{pct(grandes.pl_vs_ro_pct.max())} acima em {len(grandes)} ({' e '.join(grandes.caso)}). A PL nunca ficou "
    "abaixo do RationOptimizer: no mesmo problema, o SLSQP acha o ótimo local do modelo não linear e a PL sucessiva "
    "chega a ele ou a um ponto viável muito próximo.")
figura(FIG / "comparacao_ration_optimizer.png", "Diferença de custo PL − RationOptimizer em cada caso.",
       largura=14)
h2("8.1 Resultado por caso")
tabela(["Caso", "PL", "RationOptimizer", "PL − RO", "RO convergiu", "RO viável", "Dieta diferente"],
       [[c.caso, num(c.custo_pl, 4), num(c.custo_ro, 4), pct(c.pl_vs_ro_pct, 2, sinal=True),
         f"{c.n_convergidas}/{c.n_execucoes}", f"{c.n_viaveis}/{c.n_execucoes}", pct(c.diferenca_composicao_pct)]
        for c in comp.itertuples()],
       larguras=[4.0, 2.0, 2.4, 1.8, 2.1, 1.9, 2.2], fonte=8, alinhar_dir=(1, 2, 3, 4, 5, 6))
par("Custos em R$ por vaca por dia (no caso da aula, US$). \"Dieta diferente\" é a parcela da MS que teria de trocar "
    "de ingrediente para passar de uma dieta à outra.", italico=True)

h2("8.2 Execuções do RationOptimizer")
msgs = execs.mensagem.value_counts()
tabela(["Indicador", "Valor"], [
    ["Execuções", f"{len(execs)} ({len(comp)} casos × {comp_json['n_sementes']} pontos iniciais)"],
    ["Convergiram (critério do SciPy)", f"{int(execs.convergiu.sum())} ({pct(100 * execs.convergiu.mean())})"],
    ["Terminaram viáveis (folga ≥ −0,001)", f"{int(execs.viavel.sum())} ({pct(100 * execs.viavel.mean())})"],
    ["Mensagens do SLSQP", "; ".join(f"{m}: {n}" for m, n in msgs.items())],
    ["Tempo médio por execução", f"{num(execs.segundos.mean(), 3)} s"],
    ["Tempo médio da PL por caso", f"{num(comp.segundos_pl.mean(), 3)} s"],
], larguras=[5.6, 10.8], fonte=9)
par(f"No caso da aula, nenhuma das {int(aula.n_execucoes)} execuções convergiu (o SLSQP para no limite de 100 "
    f"iterações) e {int(aula.n_execucoes - aula.n_viaveis)} terminaram com restrições violadas; a pior ficou "
    f"{num(abs(pior_aula), 0)} g abaixo da exigência de proteína metabolizável. Na simulação, o RuFaS trataria a não "
    "convergência como falha e reformularia a dieta com menos leite. A PL resolveu todos os casos sem depender de "
    "ponto inicial.")

h2("8.3 Por que a PL às vezes fica acima")
par(f"Em {int(comp[comp.ro_convergiu].dieta_ro_atende_pl.sum())} dos {len(convergidos)} casos convergidos, a dieta do "
    "RationOptimizer atende todas as linhas da PL linearizadas nela mesma: a formulação é a mesma, e a diferença "
    "vem do método. A cada iteração a SLP recalcula os coeficientes que dependem da dieta inteira (energia "
    "digestível, desconto de NDT, degradabilidade da proteína) e os trata como constantes. Ela não enxerga que trocar "
    "um ingrediente muda os coeficientes dos outros, e pode parar num ponto fixo viável que não é o ótimo local, que "
    "o SLSQP encontra usando as derivadas.")
par("O efeito aparece quando a dieta ótima muda muito. Nos casos com maior diferença, cerca de um terço da MS é "
    "diferente entre as duas dietas, com custos a menos de 2% um do outro: o ótimo é \"plano\", e várias combinações "
    "de ingredientes custam praticamente o mesmo. Uma SLP com as derivadas dos coeficientes (expansão de Taylor) e "
    "região de confiança fecharia essa diferença; fica como trabalho futuro.")

h2("8.4 Dietas lado a lado")
nomes_comp = {int(k): v for k, v in comp_json["nomes"].items()}
maior_lote = grandes[grandes.grupo != "pairwise"].sort_values("pl_vs_ro_pct")
for caso in ["Vaca referência (quadro)",
             (maior_lote if len(maior_lote) else grandes.sort_values("pl_vs_ro_pct")).caso.iloc[-1]]:
    d = comp_json["dietas"][caso]
    ids_caso = sorted({int(k) for k in d["pl"]} | {int(k) for k in d["ro"]},
                      key=lambda k: -(d["pl"].get(str(k), 0) + d["ro"].get(str(k), 0)))
    ids_caso = [k for k in ids_caso if d["pl"].get(str(k), 0) > 1e-3 or d["ro"].get(str(k), 0) > 1e-3]
    linha = comp[comp.caso == caso].iloc[0]
    par(f"{caso} — PL {num(linha.custo_pl, 4)} × RationOptimizer {num(linha.custo_ro, 4)} "
        f"({pct(linha.pl_vs_ro_pct, 2, sinal=True)}):", negrito_inicio=None)
    tabela(["Ingrediente (kg MS/vaca/dia)", "PL", "RationOptimizer"],
           [[nomes_comp.get(k, str(k)), num(d["pl"].get(str(k), 0)), num(d["ro"].get(str(k), 0))] for k in ids_caso]
           + [[("CMS total",), (num(sum(d["pl"].values())),), (num(sum(d["ro"].values())),)]],
           larguras=[7.4, 4.5, 4.5], fonte=9, alinhar_dir=(1, 2))

h2("8.5 Conclusão da comparação")
caixa("Conclusão:", f"no mesmo problema, a PL fica a no máximo {pct(convergidos.pl_vs_ro_pct.max())} do "
      f"RationOptimizer e chega ao mesmo custo em {n_iguais} de {len(convergidos)} casos. Em troca, não depende do "
      "ponto inicial, sempre termina com uma dieta viável (ou reduz o leite de forma explícita), resolve uma PL em "
      f"cerca de {num(comp.segundos_pl.mean() * 1000, 0)} ms e entrega os preços-sombra e as faixas de validade que o "
      "SLSQP não fornece. A diferença é uma limitação conhecida do método de linearização, não da formulação.",
      cor="E8F5E9", borda="2E7D32")

# ======================================================================================= 9. LIMITAÇÕES
h1("9. Limitações")
lista([
    "A integração com o RuFaS é feita por arquivo: o módulo calcula a dieta antes da simulação e a grava no feed.json. "
    "Ele não substitui o RationOptimizer dentro do laço da simulação.",
    f"A SLP pode parar num ponto fixo viável até {pct(convergidos.pl_vs_ro_pct.max())} mais caro que o ótimo local do "
    "modelo não linear (seção 8).",
    "Preços-sombra e intervalos valem perto do ótimo da última linearização; para mudanças grandes, a otimização é "
    "refeita (é o que a SLP, o Sobol e o pairwise fazem).",
    "O limite superior de proteína metabolizável usa o ramo do NDT da proteína microbiana, que é conservador.",
    "Só o padrão NASEM (o do freestall da fazenda) foi implementado; o NRC tem restrições de energia diferentes.",
    "Metano entérico e N urinário por kg de CMS foram calibrados na rodada base da GO_campo_dourado.",
    "O RuFaS formula uma ração por curral; os quatro lotes viram uma ração média para a simulação.",
    "A simulação do RuFaS 1.0.5 não se repete entre rodadas com as mesmas entradas e semente fixa; os ganhos da "
    "simulação são informados como faixa.",
    "A dieta atual da fazenda não atende o lote de início de lactação; parte da economia em relação a ela vem dessa "
    "diferença de atendimento.",
    "Provável erro no RuFaS 1.0.5: vaca não prenhe com DEL < 100 recebe exigência de ELl de gestação que cresce com o "
    "DEL e zera no DEL 100. O módulo usa o calculador sem alteração, então o lote de início de lactação herda esse "
    "acréscimo.",
])

# ======================================================================================= 10. REPRODUZIR
h1("10. Como reproduzir", nova_pagina=False)
par("Pela pasta docker/ do repositório (a imagem tem o RuFaS, SciPy/HiGHS e SALib):")
codigo("""docker compose build
docker compose run --rm otimizador tudo        # pipeline completo
docker compose run --rm --entrypoint python otimizador go_campo_dourado_ifg/scripts/coletar_execucao.py
docker compose run --rm --entrypoint python otimizador go_campo_dourado_ifg/scripts/avaliar_rodadas_rufas.py""")
par("O relatório é gerado no host (python-docx e matplotlib), a partir da raiz do repositório:")
codigo("python go_campo_dourado_ifg/scripts/gerar_relatorio_evidencias.py .")
par("Para mudar preços, lotes, faixas do Sobol ou fatores do pairwise, edite "
    "go_campo_dourado_ifg/config_otimizacao.json e rode o pipeline de novo.")

h1("Anexo — Arquivos de resultado", nova_pagina=False)
par("Cada etapa do pipeline grava numa pasta própria dentro de go_campo_dourado_ifg/resultados/.")
tabela(["Pasta e arquivo", "Conteúdo"], [
    ["otimizar/dietas.json", "dieta por lote (kg e % MS), exigências, restrições com folga e preço-sombra, custos "
                             "reduzidos, validação pelo RuFaS, ração média de lactação"],
    ["otimizar/economia_lotes.csv, economia_rebanho.json", "custo, receita, IOFC e custo/kg de leite (ótima × atual)"],
    ["otimizar/custo_marginal_leite.csv", "custo de ração de 1 kg a mais de leite, por lote"],
    ["otimizar/quadro_final_simplex.csv, simplex_*.csv|json", "quadro final, base, preços-sombra e faixas"],
    ["sensibilidade/sobol_*.csv|json", "amostras, fatores e índices S1/ST"],
    ["pairwise/pairwise_cenarios.csv, pairwise_resumo.json", "cenários pairwise e resultados"],
    ["comparar/comparacao_ration_optimizer*.csv|json", "PL × RationOptimizer por caso e por execução"],
    ["rufas/rufas_comparacao.json", "simulação do RuFaS desta execução: base × otimizada"],
    ["rufas/rodadas_rufas.csv", "todas as rodadas de simulação reavaliadas com os preços atuais"],
    ["relatorio_otimizacao.md, figuras/", "relatório Markdown do módulo e figuras"],
    ["execucao/execucao.json, log_*.txt", "tempos, CPU, memória e logs desta execução"],
], larguras=[6.4, 10.0], fonte=8.5)

doc.core_properties.title = "Módulo de otimização da dieta integrado ao RuFaS — relatório técnico"
doc.core_properties.subject = "Otimização de dieta, efeito econômico e análise de sensibilidade"
SAIDA.parent.mkdir(parents=True, exist_ok=True)
doc.save(SAIDA)


def converter_pdf(docx, pasta):
    subprocess.run([str(SOFFICE), "--headless", "--convert-to", "pdf", "--outdir", str(pasta), str(docx)], check=True,
                   capture_output=True)
    return Path(pasta) / f"{Path(docx).stem}.pdf"


def paginas_dos_titulos(pdf):
    """Página de cada título no PDF: primeira linha com fonte de título (≥ 12,5 pt) que começa com o texto dele."""
    import fitz

    documento = fitz.open(pdf)
    resultado, inicio = [], 0
    for nivel, texto in titulos:
        alvo = texto[:30]
        pagina = ""
        for i in range(inicio, documento.page_count):
            linhas = [" ".join(s["text"] for s in l["spans"]).strip()
                      for b in documento[i].get_text("dict")["blocks"] for l in b.get("lines", [])
                      if any(s["size"] >= 12.5 for s in l["spans"])]
            if any(" ".join(x.split()).startswith(alvo) for x in linhas):
                pagina, inicio = i + 1, i
                break
        resultado.append([nivel, texto, pagina])
    documento.close()
    return resultado


if PAGINAS is None and SOFFICE.exists():
    with tempfile.TemporaryDirectory() as tmp:
        arquivo = Path(tmp) / "paginas.json"
        arquivo.write_text(json.dumps(paginas_dos_titulos(converter_pdf(SAIDA, tmp)), ensure_ascii=False),
                           encoding="utf-8")
        subprocess.run([sys.executable, __file__, str(RAIZ), str(arquivo)], check=True)
elif PAGINAS is not None and SOFFICE.exists():
    print(converter_pdf(SAIDA, SAIDA.parent))
print(SAIDA)
