"""
Avaliação econômica da dieta.

Indicadores por vaca por dia:
* custo da dieta (R$) = Σ preçoᵢ · kg MSᵢ;
* receita do leite (R$) = leite atendido · preço do leite;
* IOFC — Income Over Feed Cost — (R$) = receita do leite − custo da dieta;
* custo de alimentação por kg de leite (R$/kg).

E para a simulação completa do RuFaS (``avaliar_saida_rufas``): receita, custo e IOFC do rebanho ao longo de anos,
usando a mesma tabela de preços para comparar cenários de forma justa.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from RUFAS.data_structures.feed_storage_to_animal_connection import NASEMFeed

from .config import ConfigOtimizacao, Lote
from .modelo import ResultadoDieta, avaliar_dieta_fixa, otimizar_lote

DIAS_ANO = 365


@dataclass
class IndicadoresLote:
    lote: str
    n_vacas: int
    leite_kg: float
    cms_kg: float
    custo_dieta: float
    receita_leite: float

    @property
    def iofc(self) -> float:
        return self.receita_leite - self.custo_dieta

    @property
    def custo_kg_leite(self) -> float:
        return self.custo_dieta / self.leite_kg if self.leite_kg > 0 else float("nan")

    @property
    def custo_kg_ms(self) -> float:
        return self.custo_dieta / self.cms_kg if self.cms_kg > 0 else float("nan")

    def como_dict(self) -> dict[str, float | str]:
        return {
            "lote": self.lote, "n_vacas": self.n_vacas, "leite_kg_vaca_dia": self.leite_kg,
            "cms_kg_vaca_dia": self.cms_kg, "custo_dieta_brl_vaca_dia": self.custo_dieta,
            "receita_leite_brl_vaca_dia": self.receita_leite, "iofc_brl_vaca_dia": self.iofc,
            "custo_alimentar_brl_kg_leite": self.custo_kg_leite, "custo_brl_kg_ms": self.custo_kg_ms,
            "iofc_lote_brl_ano": self.iofc * self.n_vacas * DIAS_ANO,
        }


def indicadores_otimos(config: ConfigOtimizacao, resultado: ResultadoDieta,
                       preco_leite: float | None = None) -> IndicadoresLote:
    preco_leite = config.preco_leite_brl_kg if preco_leite is None else preco_leite
    return IndicadoresLote(
        lote=resultado.lote.id, n_vacas=resultado.lote.n_vacas, leite_kg=resultado.leite_atendido_kg,
        cms_kg=resultado.cms_kg, custo_dieta=resultado.custo_brl,
        receita_leite=resultado.leite_atendido_kg * preco_leite,
    )


def indicadores_dieta_atual(config: ConfigOtimizacao, lote: Lote, alimentos: list[NASEMFeed]
                            ) -> tuple[IndicadoresLote, dict[str, object]]:
    """Dieta fixa do feed.json original (percentual da MS × CMS prevista), valorizada com os preços da configuração."""
    precos = {i: config.preco(i) for i in config.dieta_atual}
    avaliacao = avaliar_dieta_fixa(config, lote, alimentos, config.dieta_atual, precos)
    return IndicadoresLote(
        lote=lote.id, n_vacas=lote.n_vacas, leite_kg=lote.leite_kg, cms_kg=avaliacao["cms_kg"],
        custo_dieta=avaliacao["custo_brl"], receita_leite=lote.leite_kg * config.preco_leite_brl_kg,
    ), avaliacao


def resumo_rebanho(indicadores: list[IndicadoresLote]) -> dict[str, float]:
    n = sum(i.n_vacas for i in indicadores)
    custo = sum(i.custo_dieta * i.n_vacas for i in indicadores)
    receita = sum(i.receita_leite * i.n_vacas for i in indicadores)
    leite = sum(i.leite_kg * i.n_vacas for i in indicadores)
    return {
        "n_vacas": n,
        "leite_kg_dia": leite,
        "custo_dieta_brl_dia": custo,
        "receita_leite_brl_dia": receita,
        "iofc_brl_dia": receita - custo,
        "iofc_brl_vaca_dia": (receita - custo) / n if n else float("nan"),
        "custo_alimentar_brl_kg_leite": custo / leite if leite else float("nan"),
        "iofc_brl_ano": (receita - custo) * DIAS_ANO,
        "custo_dieta_brl_ano": custo * DIAS_ANO,
    }


def economia_rebanho(config: ConfigOtimizacao, alimentos: list[NASEMFeed]) -> dict[str, object]:
    """
    Dieta ótima × dieta atual nos lotes da configuração, somadas no rebanho.

    Usada nas variantes da etapa otimizar: a mesma conta com uma hipótese trocada de cada vez (perdas de energia fixas,
    CMS fixa, exigência de gestação corrigida).
    """
    otimos, atuais, lotes, atual_inviavel = [], [], {}, {}
    for lote in config.lotes:
        r = otimizar_lote(config, lote, alimentos)
        ind_atual, aval = indicadores_dieta_atual(config, lote, alimentos)
        otimos.append(indicadores_otimos(config, r))
        atuais.append(ind_atual)
        falhas = {k: v for k, v in aval["validacao_rufas"].items() if v < -1e-3}
        if falhas:
            atual_inviavel[lote.id] = falhas  # função de restrição do RuFaS → folga (negativa) da dieta atual
        lotes[lote.id] = {"custo_otima": r.custo_brl, "cms_otima": r.cms_kg, "custo_atual": ind_atual.custo_dieta,
                          "reducao_leite_kg": r.reducao_leite_kg, "otima_atende_rufas": r.validacao_ok,
                          "convergiu_slp": r.convergiu_slp, "nel_mcal": r.resumo_exigencias["nel_mcal"],
                          "pm_g": r.resumo_exigencias["pm_g"],
                          "elg_gestacao_mcal": float(getattr(r.exigencias, "pregnancy_energy", float("nan")))}
    otima, atual = resumo_rebanho(otimos), resumo_rebanho(atuais)
    return {
        "otima": otima, "atual": atual, "atual_inviavel": atual_inviavel, "lotes": lotes,
        "reducao_custo_pct": 100 * (1 - otima["custo_dieta_brl_dia"] / atual["custo_dieta_brl_dia"]),
        "diferenca_iofc_brl_ano": otima["iofc_brl_ano"] - atual["iofc_brl_ano"],
    }


def custo_marginal_leite(config: ConfigOtimizacao, lote: Lote, alimentos: list[NASEMFeed],
                         base: ResultadoDieta, delta_kg: float = 1.0) -> dict[str, float]:
    """
    Quanto custa, em ração, produzir ``delta_kg`` a mais de leite neste lote (a exigência é recalculada pelo NASEM).

    Se o custo marginal for menor que o preço do leite, cada kg adicional aumenta o IOFC.
    """
    mais = otimizar_lote(config, lote.com(leite_kg=lote.leite_kg + delta_kg), alimentos,
                         x_inicial=np.array(list(base.kg_ms.values())), validar=False)
    if not mais.viavel or mais.reducao_leite_kg > 0:
        return {"custo_marginal_brl_kg": float("nan"), "margem_marginal_brl_kg": float("nan")}
    marginal = (mais.custo_brl - base.custo_brl) / delta_kg
    return {"custo_marginal_brl_kg": marginal, "margem_marginal_brl_kg": config.preco_leite_brl_kg - marginal}


# ---------------------------------------------------------------------------------------------------------------
# Saída da simulação completa do RuFaS
# ---------------------------------------------------------------------------------------------------------------
COL_DIA = "AnimalModuleReporter.report_herd_statistics_data.simulation_day (day)"
COL_LEITE = "AnimalModuleReporter.report_herd_statistics_data.daily_milk_production (kg/day)"
COL_LACTANTES = "AnimalModuleReporter.report_herd_statistics_data.milking_cow_num (animals)"
PREFIXO_FED = "FeedManager.report_feed_manager_balance.purchased_feed_"
SUFIXO_FED = "_fed_to_date (kg)"


def _cabecalho(csv: Path) -> list[str]:
    with open(csv, encoding="utf-8") as arquivo:
        return arquivo.readline().rstrip("\n").split(",")


def avaliar_saida_rufas(csv: Path, config: ConfigOtimizacao) -> dict[str, object]:
    """
    Receita do leite, custo de alimentação e IOFC do rebanho inteiro ao longo da simulação do RuFaS.

    O custo usa o total fornecido de cada alimento (``purchased_feed_<id>_fed_to_date``, kg de MS) valorizado pelos
    preços da configuração, de modo que duas rodadas com dietas diferentes são comparadas pela mesma tabela de preços.
    """
    colunas = _cabecalho(csv)
    fed = {int(c[len(PREFIXO_FED):-len(SUFIXO_FED)]): c for c in colunas
           if c.startswith(PREFIXO_FED) and c.endswith(SUFIXO_FED)}
    dados = pd.read_csv(csv, usecols=[COL_DIA, COL_LEITE, COL_LACTANTES, *fed.values()])
    dias = dados[COL_DIA].dropna()
    leite = dados[COL_LEITE].dropna()
    vacas_dia = dados[COL_LACTANTES].dropna().sum()
    n_dias = len(dias)

    consumo = {i: float(dados[c].dropna().max()) for i, c in fed.items()}
    sem_preco = [i for i in consumo if _preco_ou_none(config, i) is None]
    custo_por_alimento = {i: kg * (_preco_ou_none(config, i) or 0.0) for i, kg in consumo.items()}
    custo = float(sum(custo_por_alimento.values()))
    receita = float(leite.sum() * config.preco_leite_brl_kg)
    anos = n_dias / DIAS_ANO
    return {
        "csv": str(csv),
        "dias_simulados": n_dias,
        "leite_total_kg": float(leite.sum()),
        "leite_kg_vaca_lactante_dia": float(leite.sum() / vacas_dia) if vacas_dia else float("nan"),
        "vacas_lactantes_media": float(dados[COL_LACTANTES].dropna().mean()),
        "consumo_kg_ms_por_alimento": consumo,
        "custo_por_alimento_brl": custo_por_alimento,
        "alimentos_sem_preco": sem_preco,
        "custo_alimentacao_total_brl": custo,
        "receita_leite_total_brl": receita,
        "iofc_total_brl": receita - custo,
        "iofc_brl_ano": (receita - custo) / anos if anos else float("nan"),
        "custo_alimentar_brl_kg_leite": custo / float(leite.sum()) if leite.sum() else float("nan"),
        "iofc_brl_vaca_lactante_dia": (receita - custo) / vacas_dia if vacas_dia else float("nan"),
    }


def _preco_ou_none(config: ConfigOtimizacao, rufas_id: int) -> float | None:
    try:
        return config.preco(rufas_id)
    except KeyError:
        return None
