"""Roda o pipeline do otimizacao_dieta etapa por etapa e grava as evidências de execução.

Para cada etapa: tempo de parede, CPU (usuário + sistema, somando os processos filhos), pico de memória do maior
processo e o log completo. No fim, registra o ambiente.

Uso (pela pasta docker/):
    docker compose run --rm --entrypoint python otimizador go_campo_dourado_ifg/scripts/coletar_execucao.py

Saída em go_campo_dourado_ifg/resultados/execucao/: execucao.json e log_<etapa>.txt.
"""

import json
import os
import platform
import subprocess
import sys
import time
from datetime import datetime
from importlib import metadata
from pathlib import Path

FAZ = Path(__file__).resolve().parents[1]
RAIZ = FAZ.parent
SAIDA = FAZ / "resultados" / "execucao"
ETAPAS = ["otimizar", "sensibilidade", "pairwise", "comparar", "rufas", "relatorio"]
PACOTES = ["RuFaS", "numpy", "scipy", "pandas", "SALib", "matplotlib"]


def executar(nome: str, comando: list[str]) -> dict:
    log = SAIDA / f"log_{nome}.txt"
    inicio = datetime.now()
    t0 = time.perf_counter()
    with open(log, "w", encoding="utf-8") as arquivo:
        processo = subprocess.Popen(comando, cwd=RAIZ, stdout=arquivo, stderr=subprocess.STDOUT)
        # wait4 devolve o uso de recursos do filho e dos netos que ele esperou (RuFaS, workers do Sobol).
        _, status, uso = os.wait4(processo.pid, 0)
    segundos = time.perf_counter() - t0
    processo.returncode = os.waitstatus_to_exitcode(status)
    print(f"  {nome:14s} código {processo.returncode}  {segundos:8.1f} s", flush=True)
    return {"etapa": nome, "comando": " ".join(["python"] + comando[1:]), "inicio": inicio.isoformat(timespec="seconds"),
            "segundos": round(segundos, 2), "cpu_usuario_s": round(uso.ru_utime, 2),
            "cpu_sistema_s": round(uso.ru_stime, 2), "pico_memoria_mb": round(uso.ru_maxrss / 1024, 1),
            "codigo_saida": processo.returncode, "log": log.name}


def ambiente() -> dict:
    with open("/proc/meminfo", encoding="utf-8") as arquivo:
        memoria_kb = next(int(l.split()[1]) for l in arquivo if l.startswith("MemTotal"))
    with open("/proc/cpuinfo", encoding="utf-8") as arquivo:
        cpu = next((l.split(":", 1)[1].strip() for l in arquivo if l.startswith("model name")), "")
    pacotes = {}
    for pacote in PACOTES:
        try:
            pacotes[pacote] = metadata.version(pacote)
        except metadata.PackageNotFoundError:
            pass
    return {"python": sys.version.split()[0], "sistema": platform.platform(), "cpu": cpu, "cpus": os.cpu_count(),
            "memoria_gb": round(memoria_kb / 1024 ** 2, 1), "pacotes": pacotes}


def main() -> None:
    SAIDA.mkdir(parents=True, exist_ok=True)
    print(f"Evidências em {SAIDA}")
    etapas = [executar(etapa, [sys.executable, "-m", "otimizacao_dieta", etapa]) for etapa in ETAPAS]
    resultado = {"gerado_em": datetime.now().isoformat(timespec="seconds"), "ambiente": ambiente(), "etapas": etapas}
    with open(SAIDA / "execucao.json", "w", encoding="utf-8") as arquivo:
        json.dump(resultado, arquivo, ensure_ascii=False, indent=2)
    if any(e["codigo_saida"] for e in etapas):
        raise SystemExit("Alguma etapa falhou; veja os logs.")


if __name__ == "__main__":
    main()
