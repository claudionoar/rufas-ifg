"""
Dashboard para explorar o CSV de saída do RuFaS (output/CSVs/*.csv), que é grande demais para abrir no Excel.

Só o cabeçalho é lido para montar o catálogo de variáveis. O filtro usa o mesmo mecanismo dos filtros nativos do RuFaS
(output/output_filters/): cada linha é um padrão aplicado com ``re.search`` sobre ``Classe.metodo.variavel`` (sem a
unidade) e, se a primeira linha for ``exclude``, o filtro passa a ser por exclusão. Do CSV são carregadas apenas as
colunas escolhidas para o gráfico.

Uso local (com o venv do RuFaS ativo):
    pip install "streamlit>=1.50,<2" pyarrow
    cd dashboard
    streamlit run app.py

Uso em container (a partir da pasta docker/): ``docker compose up dashboard`` e abrir http://localhost:8501.
A pasta de saída pode ser trocada pela variável de ambiente RUFAS_OUTPUT_DIR (padrão: <repositório>/output).
"""

from __future__ import annotations

import csv
import os
import re
from datetime import datetime
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.csv as pacsv
import streamlit as st

from RUFAS.util import Utility

RAIZ_REPOSITORIO = Path(__file__).resolve().parent.parent
PASTA_SAIDA = Path(os.environ.get("RUFAS_OUTPUT_DIR", RAIZ_REPOSITORIO / "output"))
PASTA_CSVS = PASTA_SAIDA / "CSVs"
PASTA_FILTROS = PASTA_SAIDA / "output_filters"

# Cabeçalho do CSV: "Classe.metodo.variavel (unidade)". Alguns nomes têm espaço ("simulation day (simulation day)").
CABECALHO = re.compile(r"^(?P<variavel>.+?) \((?P<unidade>.*)\)$")
MAX_VARIAVEIS_GRAFICO = 12
# Acima disso o navegador fica lento para desenhar a linha; os pontos passam a ser amostrados em intervalos regulares.
MAX_PONTOS_GRAFICO = 20_000


def listar_csvs(pasta: Path) -> list[Path]:
    """CSVs de saída, do mais recente para o mais antigo."""
    if not pasta.is_dir():
        return []
    return sorted(pasta.glob("*.csv"), key=lambda caminho: caminho.stat().st_mtime, reverse=True)


@st.cache_data(show_spinner=False)
def ler_catalogo(caminho: str, modificado_em: float) -> pd.DataFrame:
    """Uma linha por variável do CSV: coluna original, módulo, chave usada pelos filtros e unidade.

    ``modificado_em`` só entra na chave do cache, para que um arquivo regravado seja lido de novo.
    """
    with open(caminho, newline="", encoding="utf-8", errors="replace") as arquivo:
        cabecalho = next(csv.reader(arquivo))
    linhas = []
    for coluna in cabecalho:
        encontrado = CABECALHO.match(coluna)
        variavel, unidade = (encontrado["variavel"], encontrado["unidade"]) if encontrado else (coluna, "")
        if variavel == "DISCLAIMER":
            continue
        linhas.append({"coluna": coluna, "modulo": variavel.split(".")[0], "variavel": variavel, "unidade": unidade})
    return pd.DataFrame(linhas)


@st.cache_data(show_spinner=False, max_entries=8)
def ler_colunas(caminho: str, modificado_em: float, colunas: tuple[str, ...]) -> pd.DataFrame:
    """Lê só as colunas pedidas. O arquivo inteiro ainda é percorrido; o leitor do pyarrow faz isso em paralelo
    (o ``pandas.read_csv`` padrão passa de um minuto num CSV de ~900 MB).

    Tudo é lido como texto e convertido depois: o pyarrow infere o tipo pelo primeiro bloco do arquivo e falharia numa
    coluna que começa com inteiros e tem decimais mais abaixo. Cada coluna tem o próprio comprimento (diária, por
    evento...), e as linhas além dele ficam vazias.
    """
    tabela = pacsv.read_csv(
        caminho,
        convert_options=pacsv.ConvertOptions(
            include_columns=list(colunas),
            column_types={coluna: pa.string() for coluna in colunas},
            strings_can_be_null=True,
        ),
    )
    dados = tabela.to_pandas()
    for coluna in dados.columns:
        try:
            dados[coluna] = pd.to_numeric(dados[coluna])
        except (ValueError, TypeError):
            pass
    dados.index.name = "registro"
    return dados


def interpretar_filtro(texto: str) -> tuple[list[str], bool]:
    """Lê o texto como um filtro .txt do RuFaS (``OutputManager._load_filter_file_content``)."""
    padroes = [linha for linha in texto.splitlines() if linha]
    por_exclusao = bool(padroes) and padroes[0] == "exclude"
    return (padroes[1:] if por_exclusao else padroes), por_exclusao


def filtrar(catalogo: pd.DataFrame, modulos: list[str], padroes: list[str], por_exclusao: bool) -> pd.DataFrame:
    """Sem padrões, não filtra por nome (no RuFaS, um filtro vazio não selecionaria nada)."""
    if modulos:
        catalogo = catalogo[catalogo["modulo"].isin(modulos)]
    if padroes:
        mantidas = Utility.filter_dictionary(dict.fromkeys(catalogo["variavel"]), padroes, por_exclusao)
        catalogo = catalogo[catalogo["variavel"].isin(mantidas)]
    return catalogo


def aparar(dados: pd.DataFrame | pd.Series) -> pd.DataFrame | pd.Series:
    """Remove as linhas vazias do fim (além do comprimento da variável) e amostra séries longas demais."""
    ultima = dados.last_valid_index()
    dados = dados.loc[:ultima] if ultima is not None else dados.iloc[:0]
    passo = -(-len(dados) // MAX_PONTOS_GRAFICO)
    return dados.iloc[::passo] if passo > 1 else dados


def rotulo_grafico(coluna: str) -> str:
    """O Vega-Lite interpreta '.', '[' e ']' no nome do campo como acesso aninhado."""
    return re.sub(r"[\[\]'\"\\]", "", coluna).replace(".", " · ")


def main() -> None:
    st.set_page_config(page_title="RuFaS — resultados", layout="wide")
    st.title("RuFaS — resultados da simulação")

    csvs = listar_csvs(PASTA_CSVS)
    with st.sidebar:
        st.header("Arquivo")
        escolhido = st.selectbox(f"CSVs em {PASTA_CSVS}", csvs, format_func=lambda caminho: caminho.name)
        digitado = st.text_input("...ou informe o caminho do CSV", help="Caminho de um CSV gerado pelo RuFaS.").strip()
    caminho = Path(digitado) if digitado else escolhido
    if caminho is None:
        st.info(
            f"Nenhum CSV em {PASTA_CSVS}. Rode o RuFaS (python main.py) com "
            "output/output_filters/csv_all_variables.txt habilitado, ou informe o caminho do CSV na barra lateral."
        )
        return
    if not caminho.is_file():
        st.error(f"Arquivo não encontrado: {caminho}")
        return

    estado = caminho.stat()
    catalogo = ler_catalogo(str(caminho), estado.st_mtime)
    st.caption(
        f"{caminho.name} · {estado.st_size / 1e6:.0f} MB · "
        f"gerado em {datetime.fromtimestamp(estado.st_mtime):%d/%m/%Y %H:%M} · {len(catalogo)} variáveis"
    )

    with st.sidebar:
        st.header("Filtro")
        contagem = catalogo["modulo"].value_counts()
        modulos = st.multiselect(
            "Módulos", contagem.index.tolist(), format_func=lambda modulo: f"{modulo} ({contagem[modulo]})",
            placeholder="Todos",
        )
        filtros_rufas = sorted(PASTA_FILTROS.glob("*.txt")) if PASTA_FILTROS.is_dir() else []
        base = st.selectbox(
            "Partir de um filtro de output/output_filters/", [None, *filtros_rufas],
            format_func=lambda filtro: "(nenhum)" if filtro is None else filtro.name,
        )
        texto = st.text_area(
            "Palavras-chave ou padrões regex (um por linha)",
            value=base.read_text(encoding="utf-8") if base else "",
            key=f"padroes_{base}",
            height=140,
            help=(
                "Mesmo formato dos filtros .txt do RuFaS: cada linha é aplicada com re.search sobre "
                "Classe.metodo.variavel (diferencia maiúsculas). Escreva 'exclude' na primeira linha para "
                "ficar com as variáveis que NÃO casam."
            ),
        )

    padroes, por_exclusao = interpretar_filtro(texto)
    try:
        selecionadas = filtrar(catalogo, modulos, padroes, por_exclusao)
    except re.error as erro:
        st.error(f"Padrão regex inválido: {erro}")
        return

    st.subheader(f"{len(selecionadas)} variáveis encontradas")
    st.dataframe(selecionadas[["modulo", "variavel", "unidade"]], hide_index=True, height=260)
    if selecionadas.empty:
        return

    # Formulário: o CSV só é relido ao clicar no botão, não a cada variável marcada.
    with st.form("grafico"):
        escolhidas = st.multiselect(
            f"Variáveis para o gráfico (até {MAX_VARIAVEIS_GRAFICO})",
            selecionadas["coluna"].tolist(),
            max_selections=MAX_VARIAVEIS_GRAFICO,
            placeholder="Escolha as variáveis",
        )
        juntas = st.toggle("Todas no mesmo gráfico")
        st.form_submit_button("Carregar e plotar", type="primary")
    if not escolhidas:
        return

    with st.spinner("Lendo as colunas no CSV (o arquivo inteiro é percorrido)..."):
        dados = ler_colunas(str(caminho), estado.st_mtime, tuple(escolhidas))
    unidades = dict(zip(catalogo["coluna"], catalogo["unidade"]))
    numericas = dados.select_dtypes("number")
    outras = dados.drop(columns=numericas.columns)

    if juntas and not numericas.empty:
        st.line_chart(aparar(numericas).rename(columns=rotulo_grafico), x_label="registro")
    else:
        for coluna in numericas.columns:
            st.markdown(f"**{coluna}**")
            serie = aparar(numericas[coluna]).rename(rotulo_grafico(coluna))
            st.line_chart(serie, x_label="registro", y_label=unidades.get(coluna, ""))
    for coluna in outras.columns:
        st.markdown(f"**{coluna}** — contagem de valores")
        st.bar_chart(outras[coluna].value_counts().rename(rotulo_grafico(coluna)))

    if not numericas.empty:
        st.subheader("Resumo")
        st.dataframe(numericas.describe().T)
    st.download_button(
        "Baixar as variáveis escolhidas (CSV)",
        dados.to_csv().encode("utf-8"),
        file_name=f"{caminho.stem}_selecao.csv",
        mime="text/csv",
    )


main()
