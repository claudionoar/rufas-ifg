"""
Geração de cenários por pairwise testing (combinação de todos os pares).

Com k fatores de 3 níveis (baixo, base, alto), o fatorial completo tem 3^k cenários (729 para k = 6). O pairwise
garante que todo par de níveis de quaisquer dois fatores aparece em pelo menos um cenário, com bem menos cenários
(em torno de 15 para k = 6). Isso pega os efeitos principais e as interações de dois fatores, que são as que
o Sobol aponta quando ST − S1 é grande.

Algoritmo IPOG (In-Parameter-Order-General): começa com o fatorial completo dos dois primeiros fatores e, a cada novo
fator, cresce na horizontal (escolhe o nível que cobre mais pares novos em cada cenário existente) e na vertical
(cria cenários só para os pares que sobraram). Posições livres recebem o nível base.
"""

from __future__ import annotations

from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

from .biblioteca import carregar_alimentos
from .config import ConfigOtimizacao
from .modelo import otimizar_lote
from .sensibilidade import Fator, avaliar, fatores_da_config

NIVEIS = ("baixo", "base", "alto")
BASE = 1


def gerar_pairwise(n_niveis: list[int], nivel_livre: int = BASE) -> list[list[int]]:
    """Cenários (listas de índices de nível) que cobrem todos os pares de níveis entre fatores."""
    k = len(n_niveis)
    if k == 0:
        return []
    if k == 1:
        return [[v] for v in range(n_niveis[0])]
    testes: list[list[int | None]] = [[a, b] for a in range(n_niveis[0]) for b in range(n_niveis[1])]
    for i in range(2, k):
        faltam = {(j, vj, vi) for j in range(i) for vj in range(n_niveis[j]) for vi in range(n_niveis[i])}
        for teste in testes:  # crescimento horizontal
            def ganho(v: int) -> int:
                return sum((j, teste[j], v) in faltam for j in range(i) if teste[j] is not None)
            melhor = max(range(n_niveis[i]), key=lambda v: (ganho(v), v == nivel_livre, -v))
            teste.append(melhor)
            faltam -= {(j, teste[j], melhor) for j in range(i)}
        novos: list[list[int | None]] = []
        for j, vj, vi in sorted(faltam):  # crescimento vertical
            if any(t[i] == vi and t[j] == vj for t in novos):
                continue
            for teste in novos:
                if teste[i] == vi and teste[j] is None:
                    teste[j] = vj
                    break
            else:
                teste = [None] * (i + 1)
                teste[j], teste[i] = vj, vi
                novos.append(teste)
        testes.extend(novos)
    return [[nivel_livre if v is None else v for v in t] for t in testes]


def pares_descobertos(testes: list[list[int]], n_niveis: list[int]) -> list[tuple[int, int, int, int]]:
    """Pares (fator_a, nível_a, fator_b, nível_b) que nenhum cenário cobre — deve ser vazio."""
    cobertos = {(a, t[a], b, t[b]) for t in testes for a, b in combinations(range(len(n_niveis)), 2)}
    return [(a, va, b, vb) for a, b in combinations(range(len(n_niveis)), 2)
            for va in range(n_niveis[a]) for vb in range(n_niveis[b]) if (a, va, b, vb) not in cobertos]


def valor_do_nivel(fator: Fator, nivel: int) -> float:
    return (fator.minimo, fator.base, fator.maximo)[nivel]


def rodar_pairwise(config: ConfigOtimizacao, nomes_fatores: list[str], destino: Path | None = None
                   ) -> dict[str, object]:
    todos = {f.nome: f for f in fatores_da_config(config)}
    escolhidos = [todos[n] for n in nomes_fatores]
    testes = gerar_pairwise([3] * len(escolhidos))
    descobertos = pares_descobertos(testes, [3] * len(escolhidos))

    alimentos = carregar_alimentos(config)
    lote = config.lote(config.sensibilidade["lote"])
    base = otimizar_lote(config, lote, alimentos, validar=False)
    x0 = np.array(list(base.kg_ms.values()))
    fatores_completos = list(todos.values())

    linhas = []
    for n, teste in enumerate(testes, start=1):
        valores = {f.nome: valor_do_nivel(f, nivel) for f, nivel in zip(escolhidos, teste)}
        vetor = [valores.get(f.nome, f.base) for f in fatores_completos]
        avaliacao = avaliar(np.array(vetor), config, alimentos, lote, fatores_completos, x0)
        linha = {"cenario": f"C{n:02d}"}
        linha.update({f.nome: NIVEIS[nivel] for f, nivel in zip(escolhidos, teste)})
        linha.update({f"{f.nome}_valor": valores[f.nome] for f in escolhidos})
        linha.update(avaliacao)
        linhas.append(linha)
    tabela = pd.DataFrame(linhas)
    if destino is not None:
        destino.mkdir(parents=True, exist_ok=True)
        tabela.to_csv(destino / "pairwise_cenarios.csv", index=False)
    return {"fatores": escolhidos, "testes": testes, "tabela": tabela, "pares_descobertos": descobertos,
            "fatorial_completo": 3 ** len(escolhidos)}
