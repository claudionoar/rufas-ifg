#!/bin/sh
# Prepara o diretório de saída antes de rodar o RuFaS.
set -e

mkdir -p /app/output/output_filters /app/output/logs

# README do RuFaS: renomear _csv_all_variables.txt -> csv_all_variables.txt para habilitar a saída em CSV.
if [ -z "$(ls -A /app/output/output_filters 2>/dev/null)" ]; then
    cp -r /app/output_filters_default/. /app/output/output_filters/
fi
if [ ! -f /app/output/output_filters/csv_all_variables.txt ] && [ -f /app/output_filters_default/_csv_all_variables.txt ]; then
    cp /app/output_filters_default/_csv_all_variables.txt /app/output/output_filters/csv_all_variables.txt
fi

if [ ! -f /app/input/task_manager_metadata.json ]; then
    echo "ERRO: /app/input/task_manager_metadata.json não encontrado. Verifique o volume ../input:/app/input." >&2
    exit 1
fi

exec "$@"
