# go_campo_dourado_ifg — dieta de custo mínimo integrada ao RuFaS

Trabalho da pós-graduação em IA (IFG): "Desenvolvimento de módulo de otimização da dieta integrada ao RuFaS com
avaliação de efeito econômico, e também análise de sensibilidade." Um módulo Python formula a dieta mais barata que
atende às exigências NASEM de cada lote de vacas, avalia o efeito econômico (custo, IOFC, custo por kg de leite), faz a
análise de sensibilidade (Sobol e cenários pairwise) e leva a dieta para uma simulação de 5 anos no RuFaS.

A fazenda é uma cópia da `GO_campo_dourado`. A etapa `rufas` simula a fazenda em pares: dieta atual e dieta ótima no
curral de lactação, com as mesmas sementes (`random_seeds`), a mesma tabela de preços em todos os currais e as demais
entradas iguais. Com a mesma semente o RuFaS repete a simulação, então a diferença dentro de cada par vem da ração.
(Sem a chave `random_seeds`, o RuFaS 1.0.5 sorteia uma semente a cada rodada; a chave `random_seed`, no singular, é
ignorada.)

## O que o trabalho pede e onde está

| Pedido | Código (`otimizacao_dieta/`) | Resultado |
|---|---|---|
| Otimização da dieta: vaca e lotes do quadro, exigências NASEM, ingredientes com preço, custo por vaca/dia | `modelo.py`, `exigencias.py`, `biblioteca.py` | `resultados/otimizar/dietas.json` |
| Integração ao RuFaS: exigências, alimentos e restrições do próprio RuFaS; dieta gravada na fazenda e simulada | `integracao_rufas.py` | `resultados/rufas/` |
| Efeito econômico: custo, IOFC e custo por kg de leite, dieta ótima × dieta atual | `economia.py` | `resultados/otimizar/economia_*` |
| Análise de sensibilidade (Sobol) → cenários (pairwise testing) | `sensibilidade.py`, `pairwise.py` | `resultados/sensibilidade/`, `resultados/pairwise/` |
| Último quadro do Simplex explicado na perspectiva do negócio | `simplex.py` | `resultados/otimizar/quadro_final_simplex.csv` e seção 4 de `resultados/relatorio_otimizacao.md` |
| Comparação com o RationOptimizer do RuFaS (pedida para o relatório técnico) | `comparacao.py` | `resultados/comparar/` |

O relatório técnico (`docs/Relatorio_Tecnico_Otimizacao_Dieta_RuFaS.pdf`) reúne tudo isso com a modelagem e os dados
de execução.

## Mapa da pasta

```
go_campo_dourado_ifg/
├── README.md                  este arquivo
├── config_otimizacao.json     preços, lotes de vacas, limites de inclusão, faixas do Sobol: é o que se edita para experimentar
│
├── fazenda/                   a fazenda no formato do RuFaS
│   ├── pipeline_task_manager_metadata.json   ponto de entrada: python main.py -p <este arquivo>
│   ├── pipeline_tasks.json                   prefixo dos arquivos de saída e semente (random_seeds: [44])
│   ├── farm_metadata.json                    liga os arquivos de dados abaixo
│   ├── inputs_resolvidos/                    animal, feed, clima, solo...; o feed.json recebe a dieta ótima
│   └── cenarios/atual/, cenarios/otima/      entradas das rodadas pareadas (feed.json, metadata e uma tarefa por semente)
│
├── otimizacao_dieta/          o módulo Python e o README técnico (formulação, restrições, limitações)
│
├── resultados/                o que o módulo produz, uma pasta por etapa
│   ├── relatorio_otimizacao.md               relatório consolidado
│   ├── figuras/
│   ├── otimizar/  sensibilidade/  pairwise/  comparar/  rufas/
│   └── execucao/                             tempos, CPU, memória e logs da última execução completa
│
├── docs/
│   ├── Relatorio_Tecnico_Otimizacao_Dieta_RuFaS.pdf   o .docx fica só na máquina local; o git guarda o PDF
│   └── figuras/                              figuras desenhadas para o relatório técnico
│
└── scripts/                   coletam as evidências de execução e geram o relatório técnico (não fazem parte do módulo)
```

## Como rodar

Na pasta `docker/` do repositório (a imagem já traz o RuFaS, o SciPy/HiGHS e o SALib):

```bash
docker compose build
docker compose run --rm otimizador tudo     # as seis etapas abaixo, em ordem (~32 min, quase tudo é a simulação)
```

Cada etapa também roda sozinha, com `docker compose run --rm otimizador <etapa>`:

| # | Etapa | O que faz | Grava em |
|---|---|---|---|
| 1 | `otimizar` | PL de custo mínimo de cada lote, quadro final do Simplex, economia | `resultados/otimizar/` |
| 2 | `sensibilidade` | índices de Sobol (18.432 PLs) | `resultados/sensibilidade/` |
| 3 | `pairwise` | cenários que cobrem todos os pares de níveis dos fatores mais influentes | `resultados/pairwise/` |
| 4 | `comparar` | a mesma dieta resolvida pela PL e pelo RationOptimizer do RuFaS | `resultados/comparar/` |
| 5 | `rufas` | grava a dieta em `fazenda/inputs_resolvidos/feed.json`, monta os cenários pareados e simula 5 anos de cada um com cada semente | `resultados/rufas/` (comparação, rodadas e extratos dos CSVs) e `output/CSVs/` |
| 6 | `relatorio` | junta tudo em Markdown, com figuras | `resultados/relatorio_otimizacao.md`, `resultados/figuras/` |

A ordem importa: `pairwise` usa os índices do Sobol, `comparar` usa os cenários do pairwise e `rufas` usa a dieta da
etapa `otimizar`.

As sementes candidatas, quantos pares rodar e quantas simulações rodam ao mesmo tempo ficam em
`config_otimizacao.json` → `rufas`. As sementes são usadas em ordem até completar os pares; se uma simulação do par
para no RuFaS (o 1.0.5 às vezes falha no separador do módulo de esterco), a semente é descartada, com o erro
registrado em `rufas_comparacao.json`, e a próxima entra. Cada simulação de 5 anos leva de 6 a 8 minutos e usa cerca
de 5,5 GB de memória. Os CSVs completos ficam em
`output/CSVs/`, fora do git (centenas de MB cada); `resultados/rufas/extratos/` guarda só as colunas usadas nos
resultados.

Para rodar só a simulação da fazenda, com o `feed.json` atual:

```bash
docker compose run --rm rufas python main.py -p go_campo_dourado_ifg/fazenda/pipeline_task_manager_metadata.json -g
```

A etapa `rufas` sobrescreve `fazenda/inputs_resolvidos/feed.json`. O original fica em `feed_original.json`, na mesma
pasta; para voltar à ração original, copie-o por cima do `feed.json`.

## Como regenerar o relatório técnico

As evidências de execução são coletadas no Docker:

```bash
docker compose run --rm --entrypoint python otimizador go_campo_dourado_ifg/scripts/coletar_execucao.py       # pipeline com tempo, CPU e memória → resultados/execucao/
docker compose run --rm --entrypoint python otimizador go_campo_dourado_ifg/scripts/avaliar_rodadas_rufas.py  # opcional: todas as simulações guardadas → resultados/rufas/rodadas_rufas.csv
```

O relatório é gerado no host, a partir da raiz do repositório (python-docx, pandas, matplotlib, PyMuPDF e
LibreOffice). O script grava o .docx e o .pdf em `docs/`:

```bash
python go_campo_dourado_ifg/scripts/gerar_relatorio_evidencias.py .
```
