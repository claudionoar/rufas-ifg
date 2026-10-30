"""Exigências nutricionais de um lote, calculadas pelos calculadores do próprio RuFaS (NASEM 2021)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from RUFAS.biophysical.animal.animal_module_constants import AnimalModuleConstants
from RUFAS.biophysical.animal.data_types.animal_types import AnimalType
from RUFAS.biophysical.animal.data_types.nutrition_data_structures import NutritionRequirements
from RUFAS.biophysical.animal.nutrients.nasem_requirements_calculator import NASEMRequirementsCalculator

from .config import Lote


def calcular_exigencias(lote: Lote, ndf_dieta_anterior: float = AnimalModuleConstants.DEFAULT_NDF_PERCENTAGE
                        ) -> NutritionRequirements:
    """
    Exigências por vaca por dia para o lote.

    ``ndf_dieta_anterior`` é a fração de FDN da dieta anterior; o NASEM usa esse valor na predição de CMS e de proteína.
    O RuFaS usa 0,30 quando ainda não há dieta anterior (``DEFAULT_NDF_PERCENTAGE``).
    """
    return NASEMRequirementsCalculator.calculate_requirements(
        body_weight=lote.peso_vivo,
        mature_body_weight=lote.peso_adulto,
        day_of_pregnancy=lote.dias_gestacao,
        body_condition_score_5=lote.ecc,
        days_in_milk=lote.del_,
        average_daily_gain_heifer=None,
        animal_type=AnimalType.LAC_COW,
        parity=lote.parto,
        calving_interval=lote.intervalo_partos,
        milk_fat=lote.gordura_pct,
        milk_true_protein=lote.proteina_pct,
        milk_lactose=lote.lactose_pct,
        milk_production=lote.leite_kg,
        housing=lote.alojamento,
        distance=lote.distancia_m,
        lactating=True,
        ndf_percentage=ndf_dieta_anterior,
        process_based_phosphorus_requirement=0.0,
    )


@contextmanager
def sem_gestacao_para_nao_prenhe() -> Iterator[None]:
    """
    Variante de análise para o provável erro do RuFaS 1.0.5 na exigência de gestação da vaca não prenhe.

    Em ``NASEMRequirementsCalculator`` a vaca não prenhe fica com peso de útero 0 (nasem_requirements_calculator.py:216)
    e, com DEL < 100, o termo de involução −0,2·DEL·(útero − 0,204) vira ganho positivo de tecido gestante (linhas
    352–353): 7,63 Mcal de ELl no DEL 45, que crescem até o DEL 99 e zeram no DEL 100. Dentro deste bloco a exigência
    de gestação da vaca não prenhe é zero (com a involução do útero do NASEM seria cerca de −0,07 Mcal no DEL 45).
    Só os cálculos do módulo mudam; a simulação do RuFaS continua com o cálculo original.
    """
    original = NASEMRequirementsCalculator.__dict__["_calculate_pregnancy_energy_requirements"]
    funcao = original.__func__

    def sem_erro(cls, lactating, day_of_pregnancy, days_in_milk, gravid_uterine_weight, uterine_weight):
        if day_of_pregnancy is None:
            return 0.0, 0.0
        return funcao(cls, lactating, day_of_pregnancy, days_in_milk, gravid_uterine_weight, uterine_weight)

    NASEMRequirementsCalculator._calculate_pregnancy_energy_requirements = classmethod(sem_erro)
    try:
        yield
    finally:
        NASEMRequirementsCalculator._calculate_pregnancy_energy_requirements = original


def resumo_exigencias(req: NutritionRequirements, buffer_fosforo_pct: float = 0.0) -> dict[str, float]:
    """Exigências na forma usada pelas restrições do modelo (mesmos critérios do RationOptimizer NASEM)."""
    return {
        "cms_kg": float(req.dry_matter),
        "nel_mcal": float(req.total_energy_requirement),
        "pm_g": float(req.metabolizable_protein),
        "ca_g": float(req.calcium),
        "p_g": float(max(req.phosphorus, req.process_based_phosphorus) * (1 + buffer_fosforo_pct / 100)),
    }
