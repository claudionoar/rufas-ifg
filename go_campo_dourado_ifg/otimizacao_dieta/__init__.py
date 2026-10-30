"""
Módulo de otimização de dieta de custo mínimo integrado ao RuFaS.

Reaproveita do RuFaS:
- os calculadores de exigência nutricional (NASEM 2021 / NRC 2001);
- a biblioteca de composição de alimentos (NASEM_Comp.csv) e a classe ``NASEMFeed``;
- as equações de oferta de energia, proteína metabolizável, Ca e P (``NutritionSupplyCalculator``);
- as funções de restrição do ``RationOptimizer``, usadas para validar a solução;
- o simulador completo (``python main.py``), usado para a avaliação econômica de longo prazo.

O que o módulo acrescenta:
- formulação por Programação Linear (HiGHS) com linearização sucessiva das equações não lineares do RuFaS;
- um Simplex tabular próprio, que expõe o quadro final e sua leitura econômica (preços-sombra, custos reduzidos,
  intervalos de otimalidade e de viabilidade);
- avaliação econômica (custo da dieta, IOFC — Income Over Feed Cost, custo por kg de leite);
- análise de sensibilidade global de Sobol (SALib) e geração de cenários por pairwise testing.
"""

__version__ = "0.1.0"
