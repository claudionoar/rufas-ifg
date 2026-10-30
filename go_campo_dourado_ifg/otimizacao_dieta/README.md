# otimizacao_dieta — dieta de custo mínimo integrada ao RuFaS

Formula a dieta mais barata que atende às exigências NASEM de cada lote de vacas, avalia o efeito econômico
(custo, IOFC, custo por kg de leite), explica o quadro final do Simplex em linguagem de negócio, faz análise de
sensibilidade global (Sobol) e gera cenários por pairwise testing. A dieta ótima pode ser gravada na fazenda e
simulada no RuFaS por vários anos.

Caso de estudo: `go_campo_dourado_ifg/` (cópia do `GO_campo_dourado`; mapa da pasta no [README](../README.md)). Dentro
dela, o módulo fica em `otimizacao_dieta/`, a configuração em `config_otimizacao.json`, a fazenda do RuFaS em `fazenda/`
e os resultados em `resultados/`, uma pasta por etapa.

## Como rodar

Pela pasta `docker/` (a imagem já tem RuFaS, SciPy/HiGHS e SALib):

```bash
docker compose build
docker compose run --rm otimizador tudo           # pipeline completo (~30 min; quase tudo é a simulação do RuFaS)
docker compose run --rm otimizador otimizar       # PL por lote, Simplex, economia                → resultados/otimizar/
docker compose run --rm otimizador sensibilidade  # Sobol (N = 1024 → 18.432 PLs, ~40 s)           → resultados/sensibilidade/
docker compose run --rm otimizador pairwise       # cenários a partir dos fatores do Sobol         → resultados/pairwise/
docker compose run --rm otimizador comparar       # PL × RationOptimizer no mesmo problema (~45 s) → resultados/comparar/
docker compose run --rm otimizador rufas          # simulação pareada de 5 anos (atual × ótima)   → resultados/rufas/
docker compose run --rm otimizador rufas --sem-rodar   # só grava o feed.json e os cenários pareados
docker compose run --rm otimizador relatorio      # resultados/relatorio_otimizacao.md e resultados/figuras/

# Evidências para o relatório técnico (docs/Relatorio_Tecnico_Otimizacao_Dieta_RuFaS.pdf)
docker compose run --rm --entrypoint python otimizador go_campo_dourado_ifg/scripts/coletar_execucao.py      # pipeline com tempos, CPU, memória e logs
docker compose run --rm --entrypoint python otimizador go_campo_dourado_ifg/scripts/avaliar_rodadas_rufas.py # opcional: reavalia todas as simulações guardadas
```

O relatório técnico é gerado no host, a partir da raiz do repositório (python-docx, matplotlib, PyMuPDF e LibreOffice):
`python go_campo_dourado_ifg/scripts/gerar_relatorio_evidencias.py .`

Fora do Docker: Python 3.12/3.13 com `pip install .` na raiz do repositório. Rode também da raiz, que é de onde
o código completo do RuFaS é importado, com `go_campo_dourado_ifg` no `PYTHONPATH`:

```bash
PYTHONPATH=go_campo_dourado_ifg python -m otimizacao_dieta <etapa>        # bash
$env:PYTHONPATH = "go_campo_dourado_ifg"; python -m otimizacao_dieta <etapa>   # PowerShell
```

## Como funciona

```
config_otimizacao.json ──► lotes (PV, parto, DEL, leite, gordura...)  ──► exigencias.py  (NASEMRequirementsCalculator do RuFaS)
                       └─► ingredientes (rufas_id, R$/kg MS, mín, máx) ──► biblioteca.py (NASEM_Comp.csv → NASEMFeed do RuFaS)
                                                      │
                                                      ▼
                       modelo.py  — PL min Σ cᵢxᵢ com as restrições NASEM do RationOptimizer, resolvida pelo HiGHS
                                    e relinearizada até convergir (SLP); validação pelas funções originais do RuFaS
                                                      │
          ┌──────────────────────┬────────────────────┼────────────────────┬──────────────────────────┐
          ▼                      ▼                    ▼                    ▼                          ▼
     simplex.py            economia.py         sensibilidade.py        pairwise.py            integracao_rufas.py
  quadro final, duais,   IOFC, custo/kg,      Sobol (SALib) sobre    cenários IPOG que      feed.json com a ração ótima
  intervalos de          custo marginal       preços, animal e       cobrem todos os pares  → python main.py → CSV →
  otimalidade/viab.      do leite             preço do leite         de níveis              economia.avaliar_saida_rufas
                                                      │
                                                      ▼
                                   relatorio.py → resultados/relatorio_otimizacao.md
```

### Restrições (as mesmas do `RationOptimizer`, padrão NASEM, vaca em lactação)

| Restrição | Forma na PL |
|---|---|
| Ca e P absorvíveis ≥ exigência | linear (digestibilidade por tipo de alimento, como no RuFaS) |
| PM ≥ exigência | `0,64·m + Σ(PNDR digestível + 4,72)·xᵢ`, com `m ≤ PMic(NDT)` e `m ≤ PMic(PDR)` — o mínimo do RuFaS vira duas restrições lineares exatas |
| PM ≤ 1,5 × exigência | usa o ramo do NDT (conservador) |
| FDN 25–45% da MS, FDN de forragem ≥ 15%, EE ≤ 7% | lineares (`Σ(FDNᵢ − 25)·xᵢ ≥ 0` etc.) |
| CMS prevista ± 30% | linear (`DMI_CONSTRAINT_FRACTION` do RuFaS) |
| ELl ≥ exigência | `0,66·(EDᵢ − perdas)`, com EDᵢ da equação NASEM do RuFaS avaliada na dieta atual da iteração |

Digestibilidade de FDN/amido, desconto de NDT e degradabilidade da proteína dependem da dieta inteira. A cada
iteração os coeficientes são recalculados com as funções do próprio RuFaS no ponto da última solução, e a PL é
resolvida de novo (Programação Linear Sucessiva). Nos lotes converge em poucas iterações; perto de ótimos "planos"
pode oscilar entre vértices sem convergir. Se não convergir em `max_iter_slp` (40) iterações, o módulo fica com a
iteração mais barata que atende todas as restrições do RuFaS (ou com a última, se nenhuma atender) e marca
`convergiu_slp = False`. A solução final passa pelas funções `calcium_constraint`, `NASEM_net_energy_constraint` etc.
do RuFaS (todas ≥ 0 nos quatro lotes do caso de estudo); `validacao_ok` diz se a dieta vale no modelo do RuFaS. O
Sobol e o pairwise conferem cada dieta e registram a convergência.

A etapa `comparar` resolve o mesmo problema com a PL e com o otimizador original do RuFaS (RationOptimizer, SLSQP),
com as perdas de energia fixas na CMS prevista, como o RationOptimizer as recebe na simulação. São 23 casos: o caso de
referência da aula, os 4 lotes e os 18 cenários pairwise, com 10 pontos iniciais sorteados por caso. Nos 22 casos em
que o RationOptimizer converge, a PL chega ao mesmo custo em 11, fica até 0,29% acima em 9 e 1,6% a 1,8% acima em 2
(início de lactação). A dieta do RationOptimizer atende todas as linhas da PL, então a formulação é a mesma; a
diferença vem da SLP (ver Limitações). No caso da aula, o RationOptimizer não converge em nenhuma das 10 execuções.

## Arquivos de resultado

Cada etapa grava em `resultados/<etapa>/`. As que dependem de outra leem de lá: `pairwise` usa os índices de
`sensibilidade/`, `comparar` usa os cenários de `pairwise/` e `rufas` usa a ração de `otimizar/dietas.json`.

| Pasta | Arquivo | Conteúdo |
|---|---|---|
| `otimizar/` | `dietas.json` | dieta por lote (kg e % MS), exigências, restrições com folga e preço-sombra, custos reduzidos, validação RuFaS, ração média de lactação |
| | `economia_lotes.csv`, `economia_rebanho.json` | custo, receita, IOFC e custo/kg de leite (dieta ótima × dieta atual do GO) |
| | `economia_variantes.json` | a mesma comparação no rebanho com uma hipótese trocada de cada vez: sem o erro de gestação, perdas fixas, CMS fixa |
| | `custo_marginal_leite.csv` | custo de ração de 1 kg a mais de leite, por lote |
| | `quadro_final_simplex.csv` | quadro final completo (base, coeficientes, lado direito, linha de custos reduzidos) |
| | `simplex_faixas_restricoes.csv`, `simplex_faixas_precos.csv`, `simplex_base.json` | preços-sombra com faixa de validade; intervalos de otimalidade dos preços |
| `sensibilidade/` | `sobol_amostras.csv`, `sobol_fatores.csv`, `sobol_indices.csv`, `sobol_resumo.json` | amostras, fatores e índices S1/ST para custo, IOFC e custo/kg |
| `pairwise/` | `pairwise_cenarios.csv`, `pairwise_resumo.json` | cenários pairwise e seus resultados |
| `comparar/` | `comparacao_ration_optimizer.csv`, `..._execucoes.csv`, `.json` | PL × RationOptimizer por caso, cada execução do SLSQP e as duas dietas |
| `rufas/` | `rufas_comparacao.json`, `rufas_rodadas.csv` | simulação pareada: dieta atual × ótima por semente, diferenças e resumo (média, extremos, IC 95%) |
| | `extratos/<prefixo>.csv` | colunas usadas de cada CSV do RuFaS (leite, vacas, alimento fornecido, ração por curral, metano, N urinário) |
| | `rodadas_rufas.csv` | opcional: todas as simulações guardadas em `output/CSVs/`, reavaliadas com os preços atuais (`scripts/avaliar_rodadas_rufas.py`) |
| (raiz) | `relatorio_otimizacao.md`, `figuras/` | relatório consolidado |
| `execucao/` | `execucao.json`, `log_<etapa>.txt` | tempos, CPU, memória e logs de cada etapa (`scripts/coletar_execucao.py`) |

## Limitações conhecidas

- Só o padrão NASEM (o do freestall). O NRC tem restrições de energia diferentes e ainda não foi implementado.
- Preços-sombra valem na vizinhança do ótimo da última linearização.
- A SLP recalcula os coeficientes na dieta da iteração e os trata como constantes, sem as derivadas. Ela para num
  ponto fixo viável que pode ser um pouco mais caro que o ótimo local do modelo não linear: até 1,8% nos casos da
  etapa `comparar`, nos de início de lactação.
- Por padrão, as perdas de energia em metano e N urinário são proporcionais ao consumo
  (`parametros_modelo.perdas_energia = "por_kg_consumo"`). O RationOptimizer recebe totais fixos do curral; a opção
  `"fixas"` reproduz isso e é a usada na etapa `comparar`.
- Metano e N urinário por kg de CMS foram calibrados na rodada base do GO_campo_dourado.
- `ration.phosphorus_requirement_buffer` (animal.json) não é lido por nenhum código do RuFaS 1.0.5; o módulo tem o
  próprio `buffer_fosforo_pct` (0 por padrão, para ficar igual ao RuFaS).
- O RuFaS formula uma ração por curral; os quatro lotes viram uma ração média para a simulação.
- Bagaço de cana não existe na biblioteca NASEM do RuFaS.
- O RuFaS 1.0.5 lê a semente da chave `random_seeds` (lista) do arquivo de tarefas; sem ela usa 0, que sorteia uma
  semente a cada rodada (`task_manager.py:487` e `1553`). A chave `random_seed`, no singular, é ignorada. Com
  `random_seeds` a simulação se repete exatamente; a etapa `rufas` compara cenários com as mesmas sementes. Com
  algumas sementes o RuFaS 1.0.5 para no meio da simulação (separador do módulo de esterco); a etapa descarta a
  semente, registra o erro e usa a próxima candidata.
- Provável erro no RuFaS 1.0.5 (`nasem_requirements_calculator.py`): vaca não prenhe com DEL < 100 recebe exigência
  de ELl de "gestação" que cresce com o DEL e zera no DEL 100, porque `uterine_weight` é 0 para vaca não prenhe e o
  ganho uterino usa `−0,2 · DEL · (peso_útero − 0,204)`. O módulo usa o calculador sem alteração;
  `exigencias.sem_gestacao_para_nao_prenhe()` dá a variante sem o erro (`otimizar/economia_variantes.json`). A
  simulação do RuFaS continua com o cálculo original.
