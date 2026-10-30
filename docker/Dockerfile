# Imagem para executar o RuFaS (Ruminant Farm Systems Model).
# Contexto de build: raiz do repositório (ver docker-compose.yaml).
#
# RuFaS exige Python >=3.12,<3.14 — Python 3.14 quebra o numpy==2.2.0.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MPLBACKEND=Agg \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

# Opcional: redes com inspeção SSL (ex.: rede do IFG) podem bloquear o pip.
# Use --build-arg PIP_TRUSTED_HOST="pypi.org files.pythonhosted.org" ou um índice/proxy próprio.
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_TRUSTED_HOST=""
ENV PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST}

WORKDIR /app

# 1) Dependências (camada cacheada enquanto pyproject.toml não mudar)
COPY pyproject.toml README.md ./
COPY RUFAS ./RUFAS
# SALib, usado na análise de Sobol do módulo otimizacao_dieta, já vem nas dependências do RuFaS
RUN pip install --upgrade pip && pip install .

# Dashboard dos resultados (Streamlit). Camada própria para não reinstalar o RuFaS quando ela mudar.
# pyarrow: lê em paralelo só as colunas escolhidas no CSV de ~900 MB.
RUN pip install "streamlit>=1.50,<2" pyarrow

# 2) Código-fonte usado em tempo de execução.
# `python main.py` importa o pacote RUFAS do diretório /app (inclui os JSONs de RUFAS/input/metadata/properties).
COPY main.py ./
COPY output/output_filters ./output_filters_default
COPY dashboard ./dashboard
COPY docker/entrypoint.sh /usr/local/bin/rufas-entrypoint
RUN sed -i 's/\r$//' /usr/local/bin/rufas-entrypoint && chmod +x /usr/local/bin/rufas-entrypoint

# input/ e output/ são montados pelo docker-compose. Não há instrução VOLUME de propósito: ela criaria volumes
# anônimos vazios por cima dessas pastas quando o repositório inteiro é montado em /app (serviço "otimizador").

# Dashboard (serviço "dashboard" do docker-compose): streamlit run app.py em /app/dashboard
EXPOSE 8501

ENTRYPOINT ["rufas-entrypoint"]
CMD ["python", "main.py"]
