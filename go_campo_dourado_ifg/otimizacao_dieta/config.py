"""Leitura e validação do arquivo de configuração da otimização (config_otimizacao.json)."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Ingrediente:
    """Ingrediente disponível para a dieta, com preço e limites de inclusão em kg de MS por vaca por dia."""

    rufas_id: int
    nome: str
    preco_brl_kg_ms: float
    min_kg_ms: float = 0.0
    max_kg_ms: float = 100.0


@dataclass(frozen=True)
class Lote:
    """Grupo de vacas com o mesmo estágio fisiológico (as entradas do passo 1 do quadro: 'Estágio do animal')."""

    id: str
    nome: str
    n_vacas: int
    peso_vivo: float
    peso_adulto: float
    parto: int
    del_: int
    leite_kg: float
    gordura_pct: float
    proteina_pct: float
    lactose_pct: float = 4.85
    ecc: float = 3.0
    dias_gestacao: int | None = None
    intervalo_partos: int = 400
    distancia_m: float = 500.0
    alojamento: str = "Barn"

    def com(self, **mudancas: Any) -> "Lote":
        """Cópia do lote com campos alterados (usado nas análises de sensibilidade)."""
        if "del" in mudancas:
            mudancas["del_"] = mudancas.pop("del")
        return replace(self, **mudancas)


@dataclass(frozen=True)
class ParametrosModelo:
    tolerancia_cms: float = 0.30
    ch4_g_por_kg_cms: float = 13.3
    n_urina_g_por_kg_cms: float = 10.4
    buffer_fosforo_pct: float = 0.0
    reducao_leite_max_kg: float = 15.0
    passo_reducao_leite_kg: float = 0.5
    max_iter_slp: int = 40
    tol_slp: float = 1e-7
    # Perdas de energia em metano e N urinário: "por_kg_consumo" (proporcionais ao consumo da dieta) ou "fixas"
    # (totais do curral calculados na CMS prevista, como o RationOptimizer as recebe na simulação do RuFaS).
    perdas_energia: str = "por_kg_consumo"

    def __post_init__(self) -> None:
        if self.perdas_energia not in ("por_kg_consumo", "fixas"):
            raise ValueError(f"perdas_energia deve ser 'por_kg_consumo' ou 'fixas', não '{self.perdas_energia}'.")


@dataclass
class ConfigOtimizacao:
    caminho: Path
    fazenda: str
    padrao_nutricional: str
    cambio_brl_por_usd: float
    preco_leite_brl_kg: float
    parametros: ParametrosModelo
    ingredientes: list[Ingrediente]
    lotes: list[Lote]
    precos_outros_brl_kg_ms: dict[int, float]
    dieta_atual: dict[int, float]
    sensibilidade: dict[str, Any] = field(default_factory=dict)
    pairwise: dict[str, Any] = field(default_factory=dict)
    rufas: dict[str, Any] = field(default_factory=dict)

    @property
    def dir_fazenda(self) -> Path:
        """Fazenda no formato do RuFaS (metadata e inputs_resolvidos), na pasta 'fazenda' ao lado da configuração."""
        return self.caminho.parent / "fazenda"

    @property
    def dir_resultados(self) -> Path:
        return self.caminho.parent / "resultados"

    def dir_etapa(self, etapa: str) -> Path:
        """Resultados de uma etapa do pipeline: resultados/otimizar, resultados/sensibilidade etc."""
        return self.dir_resultados / etapa

    def lote(self, lote_id: str) -> Lote:
        for lote in self.lotes:
            if lote.id == lote_id:
                return lote
        raise KeyError(f"Lote '{lote_id}' não existe. Lotes disponíveis: {[l.id for l in self.lotes]}")

    def ingrediente(self, rufas_id: int) -> Ingrediente:
        for ing in self.ingredientes:
            if ing.rufas_id == rufas_id:
                return ing
        raise KeyError(f"Ingrediente {rufas_id} não está na lista de ingredientes.")

    def preco(self, rufas_id: int) -> float:
        """Preço em R$/kg MS de qualquer alimento conhecido pela configuração."""
        for ing in self.ingredientes:
            if ing.rufas_id == rufas_id:
                return ing.preco_brl_kg_ms
        if rufas_id in self.precos_outros_brl_kg_ms:
            return self.precos_outros_brl_kg_ms[rufas_id]
        raise KeyError(f"Sem preço para o alimento {rufas_id}. Inclua-o em 'ingredientes' ou 'precos_outros_brl_kg_ms'.")

    def com_precos(self, precos: dict[int, float]) -> "ConfigOtimizacao":
        """Cópia da configuração com preços de ingredientes substituídos."""
        novo = copy.copy(self)
        novo.ingredientes = [
            replace(ing, preco_brl_kg_ms=precos.get(ing.rufas_id, ing.preco_brl_kg_ms)) for ing in self.ingredientes
        ]
        return novo


def _ler_lote(dados: dict[str, Any]) -> Lote:
    dados = {k: v for k, v in dados.items() if not k.startswith("_")}
    dados["del_"] = dados.pop("del")
    return Lote(**dados)


def carregar_config(caminho: str | Path) -> ConfigOtimizacao:
    caminho = Path(caminho).resolve()
    with open(caminho, encoding="utf-8") as arquivo:
        bruto = json.load(arquivo)

    parametros = ParametrosModelo(
        **{k: v for k, v in bruto.get("parametros_modelo", {}).items() if not k.startswith("_")}
    )
    ingredientes = [Ingrediente(**{k: v for k, v in i.items() if not k.startswith("_")}) for i in bruto["ingredientes"]]
    ids = [i.rufas_id for i in ingredientes]
    if len(ids) != len(set(ids)):
        raise ValueError(f"Ingredientes repetidos em {caminho}: {ids}")
    for ing in ingredientes:
        if ing.min_kg_ms > ing.max_kg_ms:
            raise ValueError(f"Ingrediente {ing.nome}: min_kg_ms > max_kg_ms.")

    config = ConfigOtimizacao(
        caminho=caminho,
        fazenda=bruto["fazenda"],
        padrao_nutricional=bruto.get("padrao_nutricional", "NASEM").upper(),
        cambio_brl_por_usd=float(bruto["cambio_brl_por_usd"]),
        preco_leite_brl_kg=float(bruto["preco_leite_brl_kg"]),
        parametros=parametros,
        ingredientes=ingredientes,
        lotes=[_ler_lote(l) for l in bruto["lotes"]],
        precos_outros_brl_kg_ms={int(k): float(v) for k, v in bruto.get("precos_outros_brl_kg_ms", {}).items()},
        dieta_atual={int(k): float(v) for k, v in bruto.get("dieta_atual", {}).get("percentual_ms", {}).items()},
        sensibilidade=bruto.get("sensibilidade", {}),
        pairwise=bruto.get("pairwise", {}),
        rufas=bruto.get("rufas", {}),
    )
    if config.padrao_nutricional != "NASEM":
        raise NotImplementedError("Nesta versão o módulo formula apenas pelo padrão NASEM (o padrão do freestall).")
    return config
