"""
Carrega a biblioteca de composição de alimentos da fazenda (NASEM_Comp.csv) e cria objetos ``NASEMFeed`` do RuFaS.

Replica a conversão feita por ``AvailableFeedsBuilder._process_feed_library`` sem depender do ``InputManager``
(que exige carregar e validar a fazenda inteira).
"""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

from RUFAS.data_structures.feed_storage_to_animal_connection import (
    FeedCategorization,
    FeedComponentType,
    NASEMFeed,
)
from RUFAS.units import MeasurementUnits

from .config import ConfigOtimizacao, Ingrediente

# Campos que entram nas equações de oferta usadas pelo módulo; NaN em qualquer um deles invalida o alimento.
CAMPOS_OBRIGATORIOS = [
    "DM", "ash", "CP", "N_A", "N_B", "Kd", "RUP", "dRUP", "NDF", "lignin", "starch", "FA", "EE",
    "calcium", "phosphorus", "TDN", "DE", "starch_digested", "NPN_source",
]


def caminho_biblioteca(config: ConfigOtimizacao) -> Path:
    return config.dir_fazenda / "inputs_resolvidos" / "NASEM_Comp.csv"


def ler_tabela(caminho: Path) -> pd.DataFrame:
    tabela = pd.read_csv(caminho)
    return tabela.set_index("rufas_id", drop=False)


def criar_feed(linha: pd.Series, ingrediente: Ingrediente | None = None) -> NASEMFeed:
    """Converte uma linha do NASEM_Comp.csv em ``NASEMFeed``, aplicando preço e limites do ingrediente."""
    propriedades = {}
    for chave, valor in linha.items():
        if chave == "rufas_id":
            continue
        if isinstance(valor, float) and math.isnan(valor):
            valor = 0.0
        propriedades[chave] = valor
    propriedades["feed_type"] = FeedComponentType(propriedades["feed_type"])
    propriedades["Fd_Category"] = FeedCategorization(propriedades["Fd_Category"])
    propriedades["units"] = MeasurementUnits(propriedades["units"])
    propriedades["is_fat"] = bool(propriedades["is_fat"])
    propriedades["is_wetforage"] = bool(propriedades["is_wetforage"])
    if ingrediente is not None:
        propriedades["limit"] = ingrediente.max_kg_ms
        propriedades["lower_limit"] = ingrediente.min_kg_ms
    preco = ingrediente.preco_brl_kg_ms if ingrediente is not None else 0.0
    return NASEMFeed(
        rufas_id=int(linha["rufas_id"]),
        amount_available=0.0,
        on_farm_cost=preco,
        purchase_cost=preco,
        buffer=0.0,
        **propriedades,
    )


def carregar_alimentos(config: ConfigOtimizacao) -> list[NASEMFeed]:
    """Alimentos da configuração, na mesma ordem de ``config.ingredientes``."""
    tabela = ler_tabela(caminho_biblioteca(config))
    alimentos = []
    for ing in config.ingredientes:
        if ing.rufas_id not in tabela.index:
            raise ValueError(f"O alimento {ing.rufas_id} ({ing.nome}) não existe em {caminho_biblioteca(config)}.")
        linha = tabela.loc[ing.rufas_id]
        faltando = [c for c in CAMPOS_OBRIGATORIOS if pd.isna(linha[c])]
        if faltando:
            raise ValueError(f"O alimento {ing.rufas_id} ({ing.nome}) tem campos vazios na biblioteca: {faltando}")
        alimentos.append(criar_feed(linha, ing))
    return alimentos


def nomes_biblioteca(config: ConfigOtimizacao, ids: list[int]) -> dict[int, str]:
    tabela = ler_tabela(caminho_biblioteca(config))
    return {i: str(tabela.loc[i, "Name"]) if i in tabela.index else str(i) for i in ids}
