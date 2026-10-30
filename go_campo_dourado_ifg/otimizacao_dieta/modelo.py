"""
Formulação da dieta de custo mínimo por Programação Linear (PL).

    min  Σ cᵢ·xᵢ                          (R$/vaca/dia; xᵢ = kg de MS do ingrediente i)
    s.a. restrições nutricionais do RuFaS (padrão NASEM) e limites de inclusão

As restrições são as mesmas que o ``RationOptimizer`` do RuFaS aplica à vaca em lactação no padrão NASEM
(Ca, P, PM mínima e máxima, FDN mínima e máxima, FDN de forragem, gordura, CMS mínima e máxima, ELl). Algumas
equações do RuFaS não são lineares: a digestibilidade da FDN e do amido depende da CMS e do amido total, o desconto
de NDT depende da dieta inteira, a degradabilidade ruminal da proteína depende da taxa de passagem e a proteína
microbiana é o mínimo de dois termos. O módulo resolve isso assim:

* o mínimo da proteína microbiana vira uma variável auxiliar ``m`` com duas restrições ``m ≤ ...`` (exato);
* os demais coeficientes são recalculados no ponto da solução anterior e a PL é resolvida de novo até convergir
  (Programação Linear Sucessiva, SLP).

A solução final é conferida pelas funções de restrição originais do RuFaS (``validar_com_rufas``). A SLP não tem
garantia de convergir: perto de ótimos "planos" ela pode oscilar entre vértices. Se não convergir em ``max_iter_slp``
iterações, o módulo fica com a iteração mais barata que atende todas as restrições do RuFaS (ou com a última, se
nenhuma atender) e marca ``convergiu_slp = False``; a dieta sai sempre conferida, e ``validacao_ok`` diz se ela vale.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Literal

import numpy as np
from scipy.optimize import linprog

from RUFAS.biophysical.animal.animal_module_constants import AnimalModuleConstants
from RUFAS.biophysical.animal.data_types.animal_combination import AnimalCombination
from RUFAS.biophysical.animal.data_types.nutrition_data_structures import NutritionRequirements
from RUFAS.biophysical.animal.nutrients.nutrition_supply_calculator import FeedInRation, NutritionSupplyCalculator
from RUFAS.biophysical.animal.ration.ration_optimizer import RationConfig, RationOptimizer
from RUFAS.data_structures.feed_storage_to_animal_connection import FeedComponentType, NASEMFeed, NutrientStandard
from RUFAS.general_constants import GeneralConstants

from .config import ConfigOtimizacao, Lote, ParametrosModelo
from .exigencias import calcular_exigencias, resumo_exigencias

Sentido = Literal[">=", "<="]

# Fatores das equações de oferta do RuFaS (nutrition_supply_calculator.py), expressos por kg de MS do alimento.
DIGESTIBILIDADE_CA = {FeedComponentType.FORAGE: 0.3, FeedComponentType.CONC: 0.6, FeedComponentType.MINERAL: 0.95}
DIGESTIBILIDADE_P = {FeedComponentType.FORAGE: 0.64, FeedComponentType.CONC: 0.7, FeedComponentType.MINERAL: 0.8}
PM_ENDOGENA_G_POR_KG_CMS = 0.4 * 11.8
EFICIENCIA_PMIC = 0.64
# Folga mínima (na unidade de cada restrição do RuFaS: g, Mcal, kg) para uma dieta contar como atendida.
TOLERANCIA_RUFAS = 1e-3

DESCRICAO_RESTRICOES = {
    "calcio": ("Cálcio absorvível", "g"),
    "fosforo": ("Fósforo absorvível", "g"),
    "pm_min": ("Proteína metabolizável (mínimo)", "g"),
    "pm_max": ("Proteína metabolizável (máximo = 1,5 × exigência)", "g"),
    "pmic_ndt": ("Proteína microbiana limitada pelo NDT", "g"),
    "pmic_pdr": ("Proteína microbiana limitada pela PDR", "g"),
    "fdn_min": ("FDN mínima (25% da MS)", "kg"),
    "fdn_max": ("FDN máxima (45% da MS)", "kg"),
    "fdn_forragem": ("FDN de forragem mínima (15% da MS)", "kg"),
    "gordura_max": ("Extrato etéreo máximo (7% da MS)", "kg"),
    "cms_min": ("Consumo de MS mínimo", "kg"),
    "cms_max": ("Consumo de MS máximo", "kg"),
    "ell": ("Energia líquida de lactação (NASEM)", "Mcal"),
}


@dataclass
class Restricao:
    nome: str
    coeficientes: np.ndarray
    sentido: Sentido
    lado_direito: float

    @property
    def descricao(self) -> str:
        return DESCRICAO_RESTRICOES[self.nome][0]

    @property
    def unidade(self) -> str:
        return DESCRICAO_RESTRICOES[self.nome][1]


@dataclass
class ProblemaPL:
    """PL na forma min cᵀx, linhas ``Restricao`` e limites lb ≤ x ≤ ub. A última variável é ``m`` (PM microbiana)."""

    nomes_variaveis: list[str]
    custos: np.ndarray
    restricoes: list[Restricao]
    limite_inferior: np.ndarray
    limite_superior: np.ndarray

    def matrizes_linprog(self) -> tuple[np.ndarray, np.ndarray]:
        """Converte tudo para A_ub·x ≤ b_ub (linhas '>=' são multiplicadas por -1)."""
        linhas, rhs = [], []
        for r in self.restricoes:
            sinal = -1.0 if r.sentido == ">=" else 1.0
            linhas.append(sinal * r.coeficientes)
            rhs.append(sinal * r.lado_direito)
        return np.array(linhas), np.array(rhs)


@dataclass
class LinhaRestricao:
    nome: str
    descricao: str
    unidade: str
    sentido: Sentido
    valor: float
    lado_direito: float
    folga: float
    preco_sombra: float

    @property
    def ativa(self) -> bool:
        return abs(self.folga) <= 1e-6 * max(1.0, abs(self.lado_direito))


@dataclass
class ResultadoDieta:
    lote: Lote
    viavel: bool
    status: str
    leite_atendido_kg: float
    reducao_leite_kg: float
    exigencias: NutritionRequirements
    resumo_exigencias: dict[str, float]
    ingredientes: list[NASEMFeed]
    nomes: dict[int, str]
    kg_ms: dict[int, float] = field(default_factory=dict)
    custo_brl: float = float("nan")
    restricoes: list[LinhaRestricao] = field(default_factory=list)
    custo_reduzido: dict[int, float] = field(default_factory=dict)
    iteracoes_slp: int = 0
    convergiu_slp: bool = True
    problema: ProblemaPL | None = None
    validacao_rufas: dict[str, float] = field(default_factory=dict)

    @property
    def cms_kg(self) -> float:
        return float(sum(self.kg_ms.values()))

    def percentual_ms(self) -> dict[int, float]:
        total = self.cms_kg
        return {i: 100 * v / total for i, v in self.kg_ms.items()} if total > 0 else {}

    @property
    def validacao_ok(self) -> bool:
        return all(v >= -TOLERANCIA_RUFAS for v in self.validacao_rufas.values())

    @property
    def menor_folga_rufas(self) -> float:
        return min(self.validacao_rufas.values()) if self.validacao_rufas else float("nan")


def _feeds_em_racao(alimentos: list[NASEMFeed], x: np.ndarray) -> list[FeedInRation]:
    return [FeedInRation(amount=float(q), info=a) for a, q in zip(alimentos, x)]


def perdas_curral(parametros: ParametrosModelo, cms_dieta: float, cms_prevista: float) -> tuple[float, float]:
    """Metano entérico (g) e N urinário (kg) por vaca por dia, na forma em que o RationOptimizer os recebe."""
    base = cms_prevista if parametros.perdas_energia == "fixas" else cms_dieta
    return parametros.ch4_g_por_kg_cms * base, parametros.n_urina_g_por_kg_cms * base * GeneralConstants.GRAMS_TO_KG


def _coeficientes_no_ponto(alimentos: list[NASEMFeed], x: np.ndarray, peso_vivo: float,
                           parametros: ParametrosModelo, cms_prevista: float) -> dict[str, np.ndarray]:
    """Coeficientes por kg de MS de cada alimento, avaliados na dieta ``x`` (passo de linearização da SLP)."""
    feeds = _feeds_em_racao(alimentos, x)
    cms = float(np.sum(x))
    amido_total = sum(f.amount * f.info.starch * GeneralConstants.PERCENTAGE_TO_FRACTION for f in feeds)

    # Energia: ED de cada alimento pela equação NASEM do RuFaS, menos as perdas em metano e urina, rateadas por kg de
    # CMS da dieta atual. Com perdas fixas, o rateio faz Σ perdas·xᵢ = total fixo no ponto da linearização.
    ch4_g, n_urina_kg = perdas_curral(parametros, cms, cms_prevista)
    perdas_mcal = (13.28 * ch4_g * GeneralConstants.GRAMS_TO_KG + 0.0146 * n_urina_kg * GeneralConstants.KG_TO_GRAMS) / cms
    ed = np.array([
        NutritionSupplyCalculator.calculate_NASEM_digestible_energy([FeedInRation(1.0, a)], cms, peso_vivo, amido_total)
        for a in alimentos
    ])
    ell = AnimalModuleConstants.EFF_OF_ME_USE * (ed - perdas_mcal)

    # Proteína: desconto de NDT, taxas de passagem e PDR/PNDR, com as mesmas funções do RuFaS.
    desconto = NutritionSupplyCalculator.calculate_nutrient_intake_discount(feeds, peso_vivo)
    pct_conc = NutritionSupplyCalculator._calculate_percentage_of_concentrates(feeds, cms)
    passagem = NutritionSupplyCalculator._calculate_protein_passage_rates(feeds, cms, peso_vivo, pct_conc)
    pdr = NutritionSupplyCalculator._calculate_rumen_degradable_protein_percentages(feeds, passagem)
    pndr = NutritionSupplyCalculator._calculate_rumen_undegradable_protein_percentages(feeds, pdr)
    ndt_ajustado = np.array([a.TDN * desconto for a in alimentos])

    return {
        "ell": ell,
        "pmic_ndt": 0.13 * GeneralConstants.KG_TO_GRAMS * ndt_ajustado * GeneralConstants.PERCENTAGE_TO_FRACTION,
        "pmic_pdr": 0.85 * GeneralConstants.KG_TO_GRAMS
        * np.array([pdr[a.rufas_id] for a in alimentos]) * GeneralConstants.PERCENTAGE_TO_FRACTION,
        "pndr_digestivel": GeneralConstants.KG_TO_GRAMS * np.array(
            [pndr[a.rufas_id] * a.dRUP for a in alimentos]) * GeneralConstants.PERCENTAGE_TO_FRACTION ** 2,
    }


def montar_problema(alimentos: list[NASEMFeed], exigencias: dict[str, float], coef: dict[str, np.ndarray],
                    parametros: ParametrosModelo) -> ProblemaPL:
    n = len(alimentos)

    def linha(valores: np.ndarray, m: float = 0.0) -> np.ndarray:
        return np.append(np.asarray(valores, dtype=float), m)

    um = np.ones(n)
    ca = np.array([DIGESTIBILIDADE_CA.get(a.feed_type, 0.0) * a.calcium * 10 for a in alimentos])
    p = np.array([DIGESTIBILIDADE_P.get(a.feed_type, 0.0) * a.phosphorus * 10 for a in alimentos])
    fdn = np.array([a.NDF for a in alimentos])
    fdn_forragem = np.array([a.NDF if a.feed_type is FeedComponentType.FORAGE else 0.0 for a in alimentos])
    ee = np.array([a.EE for a in alimentos])
    pm_fixa = coef["pndr_digestivel"] + PM_ENDOGENA_G_POR_KG_CMS
    f = parametros.tolerancia_cms

    restricoes = [
        Restricao("calcio", linha(ca), ">=", exigencias["ca_g"]),
        Restricao("fosforo", linha(p), ">=", exigencias["p_g"]),
        Restricao("pm_min", linha(pm_fixa, EFICIENCIA_PMIC), ">=", exigencias["pm_g"]),
        # Limite superior conservador: usa o ramo do NDT, que é sempre ≥ ao mínimo usado pelo RuFaS.
        Restricao("pm_max", linha(pm_fixa + EFICIENCIA_PMIC * coef["pmic_ndt"]), "<=",
                  AnimalModuleConstants.PROTEIN_UPPER_LIMIT_FACTOR * exigencias["pm_g"]),
        Restricao("pmic_ndt", linha(-coef["pmic_ndt"], 1.0), "<=", 0.0),
        Restricao("pmic_pdr", linha(-coef["pmic_pdr"], 1.0), "<=", 0.0),
        Restricao("fdn_min", linha((fdn - AnimalModuleConstants.MINIMUM_RATION_NDF) / 100), ">=", 0.0),
        Restricao("fdn_max", linha((fdn - AnimalModuleConstants.MAXIMUM_RATION_NDF) / 100), "<=", 0.0),
        Restricao("fdn_forragem", linha((fdn_forragem - AnimalModuleConstants.MINIMUM_RATION_FORAGE_NDF) / 100),
                  ">=", 0.0),
        Restricao("gordura_max", linha((ee - AnimalModuleConstants.MAXIMUM_RATION_FAT) / 100), "<=", 0.0),
        Restricao("cms_min", linha(um), ">=", exigencias["cms_kg"] * (1 - f)),
        Restricao("cms_max", linha(um), "<=", exigencias["cms_kg"] * (1 + f)),
        Restricao("ell", linha(coef["ell"]), ">=", exigencias["nel_mcal"]),
    ]
    return ProblemaPL(
        nomes_variaveis=[str(a.rufas_id) for a in alimentos] + ["m"],
        custos=np.append([a.purchase_cost for a in alimentos], 0.0),
        restricoes=restricoes,
        limite_inferior=np.append([a.lower_limit for a in alimentos], 0.0),
        limite_superior=np.append([a.limit for a in alimentos], np.inf),
    )


def resolver_pl(problema: ProblemaPL):
    a_ub, b_ub = problema.matrizes_linprog()
    return linprog(
        problema.custos, A_ub=a_ub, b_ub=b_ub,
        bounds=list(zip(problema.limite_inferior, problema.limite_superior)), method="highs",
    )


def _ponto_inicial(alimentos: list[NASEMFeed], cms: float) -> np.ndarray:
    x = np.array([min(max(cms / len(alimentos), a.lower_limit), a.limit) for a in alimentos])
    return x * cms / x.sum() if x.sum() > 0 else x


def _resolver_slp(alimentos: list[NASEMFeed], exig: dict[str, float], peso_vivo: float,
                  parametros: ParametrosModelo, x_inicial: np.ndarray | None = None,
                  menor_folga: Callable[[np.ndarray], float] | None = None):
    """
    Resolve a sequência de PLs. Devolve (resultado, problema, iterações, convergiu).

    Sem convergência em ``max_iter_slp`` iterações, fica com a iteração mais barata cuja dieta atende as restrições do
    RuFaS (``menor_folga(x) ≥ −TOLERANCIA_RUFAS``); se nenhuma atender, ou sem ``menor_folga``, com a última.
    """
    x_lin = _ponto_inicial(alimentos, exig["cms_kg"]) if x_inicial is None else np.asarray(x_inicial, dtype=float)
    custo_anterior = None
    resultado, problema = None, None
    historico = []
    for iteracao in range(1, parametros.max_iter_slp + 1):
        coef = _coeficientes_no_ponto(alimentos, x_lin, peso_vivo, parametros, exig["cms_kg"])
        problema = montar_problema(alimentos, exig, coef, parametros)
        resultado = resolver_pl(problema)
        if resultado.status != 0:
            return resultado, problema, iteracao, False
        historico.append((resultado, problema))
        x_novo = resultado.x[:-1]
        convergiu = custo_anterior is not None and abs(resultado.fun - custo_anterior) < parametros.tol_slp \
            and np.max(np.abs(x_novo - x_lin)) < 1e-4
        if convergiu:
            return resultado, problema, iteracao, True
        custo_anterior = resultado.fun
        # Amortecimento após algumas iterações evita oscilação entre vértices.
        x_lin = x_novo if iteracao < 5 else 0.5 * (x_lin + x_novo)
    if menor_folga is not None:
        validas = [(r, p) for r, p in historico if menor_folga(r.x[:-1]) >= -TOLERANCIA_RUFAS]
        if validas:
            resultado, problema = min(validas, key=lambda par: par[0].fun)
    return resultado, problema, parametros.max_iter_slp, False


def _relatorio_restricoes(problema: ProblemaPL, resultado) -> list[LinhaRestricao]:
    x = resultado.x
    marginais = resultado.ineqlin.marginals
    linhas = []
    for r, marginal in zip(problema.restricoes, marginais):
        valor = float(r.coeficientes @ x)
        # linprog devolve d(custo)/d(b_ub); para linhas '>=' o b_ub é -b, então o preço-sombra é -marginal.
        preco = float(-marginal) if r.sentido == ">=" else float(marginal)
        folga = valor - r.lado_direito if r.sentido == ">=" else r.lado_direito - valor
        linhas.append(LinhaRestricao(r.nome, r.descricao, r.unidade, r.sentido, valor, r.lado_direito, folga, preco))
    return linhas


def validar_com_rufas(alimentos: list[NASEMFeed], kg_ms: np.ndarray, exigencias: NutritionRequirements,
                      peso_vivo: float, parametros: ParametrosModelo) -> dict[str, float]:
    """
    Avalia a dieta com as funções de restrição originais do ``RationOptimizer`` (padrão NASEM, vaca em lactação).

    Cada valor é oferta − exigência (ou limite − oferta); valores ≥ 0 indicam restrição atendida.
    """
    ch4_g, n_urina_kg = perdas_curral(parametros, float(np.sum(kg_ms)), exigencias.dry_matter)
    config_racao = RationConfig(
        NutrientStandard.NASEM,
        exigencias,
        alimentos,
        exigencias.dry_matter,
        exigencias.metabolizable_protein,
        peso_vivo,
        pen_average_enteric_methane=ch4_g,
        pen_average_urine_nitrogen=n_urina_kg,
        animal_combination=AnimalCombination.LAC_COW,
    )
    config_racao.dmi_constraint_fraction = parametros.tolerancia_cms
    otimizador = RationOptimizer()
    otimizador.set_constraints(config_racao)
    return {func.__name__: float(func(np.asarray(kg_ms, dtype=float), config_racao))
            for func in otimizador.NASEM_constraint_functions}


def otimizar_lote(config: ConfigOtimizacao, lote: Lote, alimentos: list[NASEMFeed],
                  nomes: dict[int, str] | None = None, x_inicial: np.ndarray | None = None,
                  validar: bool = True) -> ResultadoDieta:
    """
    Dieta de custo mínimo para o lote. Se não houver solução viável, reduz a produção de leite em passos
    (como o ``milk_reduction_maximum`` do RuFaS) até achar uma dieta viável ou atingir a redução máxima.
    """
    p = config.parametros
    nomes = nomes or {ing.rufas_id: ing.nome for ing in config.ingredientes}
    reducao = 0.0
    while True:
        lote_ajustado = lote.com(leite_kg=max(lote.leite_kg - reducao, 0.0))
        req = calcular_exigencias(lote_ajustado)
        exig = resumo_exigencias(req, p.buffer_fosforo_pct)

        def menor_folga(x: np.ndarray, req: NutritionRequirements = req) -> float:
            return min(validar_com_rufas(alimentos, x, req, lote.peso_vivo, p).values())

        resultado, problema, iteracoes, convergiu = _resolver_slp(alimentos, exig, lote.peso_vivo, p, x_inicial,
                                                                  menor_folga)
        if resultado.status == 0 or reducao + p.passo_reducao_leite_kg > p.reducao_leite_max_kg + 1e-9:
            break
        reducao += p.passo_reducao_leite_kg

    saida = ResultadoDieta(
        lote=lote, viavel=resultado.status == 0, status=resultado.message,
        leite_atendido_kg=lote_ajustado.leite_kg, reducao_leite_kg=reducao,
        exigencias=req, resumo_exigencias=exig, ingredientes=alimentos, nomes=nomes,
        iteracoes_slp=iteracoes, convergiu_slp=convergiu, problema=problema,
    )
    if not saida.viavel:
        return saida
    x = resultado.x[:-1]
    saida.kg_ms = {a.rufas_id: float(q) for a, q in zip(alimentos, x)}
    saida.custo_brl = float(resultado.fun)
    saida.restricoes = _relatorio_restricoes(problema, resultado)
    saida.custo_reduzido = {
        a.rufas_id: float(lo + up) for a, lo, up in
        zip(alimentos, resultado.lower.marginals[:-1], resultado.upper.marginals[:-1])
    }
    # Sem convergência a dieta é sempre conferida: é a única garantia de que ela vale no modelo do RuFaS.
    if validar or not convergiu:
        saida.validacao_rufas = validar_com_rufas(alimentos, x, req, lote.peso_vivo, p)
    return saida


def avaliar_dieta_fixa(config: ConfigOtimizacao, lote: Lote, alimentos_base: list[NASEMFeed],
                       percentual_ms: dict[int, float], precos: dict[int, float]) -> dict[str, object]:
    """
    Avalia uma receita fixa (percentual da MS), como o RuFaS faz com ``ration.user_input = true``:
    quantidade = percentual × CMS prevista. Retorna custo e o balanço nas restrições do RuFaS.
    """
    req = calcular_exigencias(lote)
    por_id = {a.rufas_id: a for a in alimentos_base}
    alimentos = [por_id[i] for i in percentual_ms]
    kg = np.array([percentual_ms[i] / 100 * req.dry_matter for i in percentual_ms])
    custo = float(sum(kg[k] * precos[i] for k, i in enumerate(percentual_ms)))
    return {
        "kg_ms": dict(zip(percentual_ms, kg.tolist())),
        "custo_brl": custo,
        "cms_kg": float(kg.sum()),
        "validacao_rufas": validar_com_rufas(alimentos, kg, req, lote.peso_vivo, config.parametros),
    }


def otimizar_com_slsqp_rufas(config: ConfigOtimizacao, lote: Lote, alimentos: list[NASEMFeed], semente: int = 44):
    """
    Resolve o problema com o otimizador original do RuFaS (SLSQP), para comparação.

    O RuFaS sorteia o ponto inicial com ``random.random()``; a semente fixa torna a comparação reprodutível. O
    RationOptimizer só aceita perdas de energia fixas (calculadas aqui na CMS prevista); para comparar com a PL no mesmo
    problema, use ``perdas_energia = "fixas"`` na PL. ``sucesso_scipy`` é o critério com que o RuFaS aceita a dieta na
    simulação; ``menor_folga`` < 0 indica restrição violada.
    """
    random.seed(semente)
    req = calcular_exigencias(lote)
    cms = req.dry_matter
    ch4_g, n_urina_kg = perdas_curral(replace(config.parametros, perdas_energia="fixas"), cms, cms)
    otimizador = RationOptimizer()
    resultado, config_racao = otimizador.attempt_optimization(
        nutrient_standard=NutrientStandard.NASEM,
        pen_average_body_weight=lote.peso_vivo,
        pen_average_enteric_methane=ch4_g,
        pen_average_urine_nitrogen=n_urina_kg,
        requirements=req,
        initial_dry_matter_requirement=cms,
        initial_protein_requirement=req.metabolizable_protein,
        pen_available_feeds=alimentos,
        animal_combination=AnimalCombination.LAC_COW,
    )
    ids = [a.rufas_id for a in config_racao.feeds_used]
    violadas = {c["fun"].__name__: float(c["fun"](resultado.x, config_racao))
                for c in RationOptimizer.find_failed_constraints(resultado.x, otimizador.cow_constraints, config_racao)}
    return {
        "sucesso": bool(resultado.success) and not violadas,
        "sucesso_scipy": bool(resultado.success),
        "menor_folga": min(float(c["fun"](resultado.x, config_racao)) for c in otimizador.cow_constraints),
        "mensagem": str(resultado.message),
        "kg_ms": dict(zip(ids, map(float, resultado.x))),
        "custo_brl": float(resultado.fun),
        "restricoes_violadas": violadas,
    }
