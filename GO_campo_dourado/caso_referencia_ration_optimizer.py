"""
Caso de referência da aula: dieta de custo mínimo pelo otimizador ORIGINAL do RuFaS (RationOptimizer, SLSQP),
padrão NASEM, "modo livre" (ration.user_input = false), sem o módulo otimizacao_dieta.

Vaca: 740 kg, 150 dias em lactação, 35 kg de leite, 4% de gordura, 3,2% de proteína.
Alimentos e preços (US$/kg MS): os da ração lac_cow do feed.json do GO_campo_dourado.

Uso (na pasta docker/):
    docker compose run --rm rufas python GO_campo_dourado/caso_referencia_ration_optimizer.py
    docker compose run --rm rufas python GO_campo_dourado/caso_referencia_ration_optimizer.py --parto 3 --semente 7
"""

import argparse
import json
import math
import random
import sys
from pathlib import Path

import pandas as pd

# O pacote RUFAS instalado na imagem só tem a pasta de topo; o código completo está na raiz do repositório
# (é por isso que o main.py, que fica lá, funciona). Este script fica em GO_campo_dourado/, um nível abaixo.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from RUFAS.biophysical.animal.data_types.animal_combination import AnimalCombination
from RUFAS.biophysical.animal.data_types.animal_types import AnimalType
from RUFAS.biophysical.animal.nutrients.nasem_requirements_calculator import NASEMRequirementsCalculator
from RUFAS.biophysical.animal.ration.ration_optimizer import RationOptimizer
from RUFAS.data_structures.feed_storage_to_animal_connection import (
    FeedCategorization,
    FeedComponentType,
    NASEMFeed,
    NutrientStandard,
)
from RUFAS.units import MeasurementUnits

PASTA = Path(__file__).resolve().parent / "inputs_resolvidos"
ARQ_FEED = PASTA / "feed.json"
ARQ_BIBLIOTECA = PASTA / "NASEM_Comp.csv"


def carregar_alimentos_lac_cow() -> list[NASEMFeed]:
    """Alimentos do curral de lactação, como o AvailableFeedsBuilder do RuFaS monta (preço em US$/kg MS)."""
    feed = json.loads(ARQ_FEED.read_text(encoding="utf-8"))
    precos = {f["feed_type"]: f["purchased_feed_cost"] for f in feed["feeds"]}
    ids_lac = [f["feed_type"] for r in feed["rations"] if r["animal_combination"] == "lac_cow" for f in r["feeds"]]
    tabela = pd.read_csv(ARQ_BIBLIOTECA).set_index("rufas_id", drop=False)
    alimentos = []
    for i in ids_lac:
        props = {k: (0.0 if isinstance(v, float) and math.isnan(v) else v)
                 for k, v in tabela.loc[i].items() if k != "rufas_id"}
        props["feed_type"] = FeedComponentType(props["feed_type"])
        props["Fd_Category"] = FeedCategorization(props["Fd_Category"])
        props["units"] = MeasurementUnits(props["units"])
        props["is_fat"], props["is_wetforage"] = bool(props["is_fat"]), bool(props["is_wetforage"])
        alimentos.append(NASEMFeed(rufas_id=int(i), amount_available=0.0, on_farm_cost=precos[i] * 0.01,
                                   purchase_cost=precos[i], buffer=0.15, **props))
    return alimentos


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--peso", type=float, default=740.0)
    ap.add_argument("--peso-adulto", type=float, default=740.1)
    ap.add_argument("--del", dest="del_", type=int, default=150)
    ap.add_argument("--leite", type=float, default=35.0)
    ap.add_argument("--gordura", type=float, default=4.0)
    ap.add_argument("--proteina", type=float, default=3.2)
    ap.add_argument("--parto", type=int, default=2)
    ap.add_argument("--ecc", type=float, default=3.0, help="escore de condição corporal (1 a 5)")
    ap.add_argument("--ch4-g-kg", type=float, default=13.3, help="metano entérico, g por kg de CMS")
    ap.add_argument("--n-urina-g-kg", type=float, default=10.4, help="N urinário, g por kg de CMS")
    ap.add_argument("--semente", type=int, default=44, help="o RuFaS sorteia o ponto inicial do SLSQP")
    a = ap.parse_args()

    req = NASEMRequirementsCalculator.calculate_requirements(
        body_weight=a.peso, mature_body_weight=a.peso_adulto, day_of_pregnancy=None,
        body_condition_score_5=a.ecc, days_in_milk=a.del_, average_daily_gain_heifer=None,
        animal_type=AnimalType.LAC_COW, parity=a.parto, calving_interval=400,
        milk_fat=a.gordura, milk_true_protein=a.proteina, milk_lactose=4.85, milk_production=a.leite,
        housing="Barn", distance=500, lactating=True, ndf_percentage=0.30,
        process_based_phosphorus_requirement=0.0,
    )
    cms = req.dry_matter
    alimentos = carregar_alimentos_lac_cow()

    random.seed(a.semente)
    otimizador = RationOptimizer()
    resultado, config = otimizador.attempt_optimization(
        nutrient_standard=NutrientStandard.NASEM,
        pen_average_body_weight=a.peso,
        pen_average_enteric_methane=a.ch4_g_kg * cms,
        pen_average_urine_nitrogen=a.n_urina_g_kg * cms / 1000,
        requirements=req,
        initial_dry_matter_requirement=cms,
        initial_protein_requirement=req.metabolizable_protein,
        pen_available_feeds=alimentos,
        animal_combination=AnimalCombination.LAC_COW,
    )
    violadas = {c["fun"].__name__: c["fun"](resultado.x, config)
                for c in RationOptimizer.find_failed_constraints(resultado.x, otimizador.cow_constraints, config)}

    print(f"Vaca: {a.peso:.0f} kg, {a.del_} DEL, {a.leite:.0f} kg de leite, {a.gordura}% gordura, "
          f"{a.proteina}% proteína, parto {a.parto}")
    print(f"Exigências NASEM: CMS {cms:.2f} kg | ELl {req.total_energy_requirement:.2f} Mcal | "
          f"PM {req.metabolizable_protein:.0f} g | Ca {req.calcium:.1f} g | P {req.phosphorus:.1f} g")
    print(f"SLSQP: {resultado.message}")
    print("\nDieta (kg de MS por vaca por dia):")
    nomes = pd.read_csv(ARQ_BIBLIOTECA).set_index("rufas_id")["Name"]
    for alimento, kg in zip(config.feeds_used, resultado.x):
        if kg > 1e-3:
            print(f"  {alimento.rufas_id:>4}  {nomes[alimento.rufas_id]:<32} {kg:7.3f} kg  × US$ {alimento.purchase_cost:.2f}"
                  f" = US$ {kg * alimento.purchase_cost:.3f}")
    print(f"\nCusto mínimo: US$ {resultado.fun:.2f} por vaca/dia  |  US$ {resultado.fun / a.leite:.3f} por kg de leite")
    print("Restrições violadas:", {k: f"{v:.1e}" for k, v in violadas.items()} or "nenhuma")


if __name__ == "__main__":
    main()
