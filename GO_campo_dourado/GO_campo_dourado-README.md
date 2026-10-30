# Fazenda Sintética — GO_campo_dourado (RuFaS)

Fazenda sintética leiteira gerada para o modelo **RuFaS**, pronta para ser
executada de forma autocontida. Esta pasta traz todos os arquivos de entrada
já resolvidos (rebanho, alimentação, clima de Goiás, solo, cultura, etc.),
o metadata com os caminhos ajustados e os arquivos de task para rodar a
simulação com um único comando.

- **Cenário:** conservador (100 vacas, manejo base)
- **Estado / clima:** Goiás (GO)
- **Período da simulação:** 2015 a 2019 (5 anos)
- **Tipo de simulação:** `full_farm`

---

## Pré-requisitos

- RuFaS clonado, com o ambiente Python (venv) e as dependências instaladas.
- O arquivo **`GO_campo_dourado.zip`** colocado na **raiz do RuFaS**
  (a pasta onde fica o `main.py`).

---

## Como executar

### 1. Entre na raiz do RuFaS
```bash
cd /caminho/para/RuFaS
```
Confirme que o `GO_campo_dourado.zip` está aqui, ao lado do `main.py`.

### 2. Extraia o ZIP
```bash
unzip GO_campo_dourado.zip
```
Isso cria a pasta `GO_campo_dourado/`. Confira o conteúdo:
```bash
ls GO_campo_dourado/
```
Você deve ver: `farm_metadata.json`, `_pipeline_task_manager_metadata.json`,
`_pipeline_tasks.json`, `README.md` e a pasta `inputs_resolvidos/`.

### 3. Rode a simulação
```bash
venv/bin/python main.py -p GO_campo_dourado/_pipeline_task_manager_metadata.json
```
> Se o seu ambiente não usa o venv em `venv/bin/python`, troque pelo Python
> onde as dependências do RuFaS estão instaladas (ex.: `python main.py -p ...`).

### 4. Resultado
Ao concluir, aparecerá no terminal:
```
Finished task: 1/1 (GO_campo_dourado) with 0 error(s), ...
```
O CSV com os resultados é gravado em:
```
output/CSVs/GO_campo_dourado_saved_variables_csv_all_variables.txt_<DATA>.csv
```

---

## Observações importantes

- **Extraia o ZIP na raiz do RuFaS** (junto do `main.py`). Os caminhos dentro
  do `farm_metadata.json` são relativos a essa pasta
  (ex.: `GO_campo_dourado/inputs_resolvidos/config.json`). Extrair em outro
  local faz a simulação não encontrar os arquivos.
- **Rode o comando a partir da raiz do RuFaS**, e não de dentro de
  `GO_campo_dourado/`.
- A simulação de 5 anos leva alguns minutos e consome bastante CPU/RAM.

---

## Conteúdo da pasta

| Item | Descrição |
| --- | --- |
| `farm_metadata.json` | Índice dos inputs da fazenda (caminhos já ajustados). |
| `_pipeline_task_manager_metadata.json` | Aponta para o task file (usado no `-p`). |
| `_pipeline_tasks.json` | Define a task de simulação (output_prefix, seed, etc.). |
| `inputs_resolvidos/` | Todos os arquivos de entrada usados na simulação. |
| `parametrizacao.json` / `.md` | Resumo da parametrização / cenário da fazenda. |
| `README.md` | Este arquivo. |
