"""
Simplex tabular de duas fases, escrito para expor o quadro final e sua leitura econômica.

O HiGHS (usado em ``modelo.py``) é mais rápido e robusto, mas não mostra o quadro. Este módulo resolve a mesma PL
(a da última iteração da SLP) e devolve:

* o quadro final (base, valores, custos reduzidos);
* preços-sombra (variáveis duais) de cada restrição e de cada limite de inclusão;
* intervalos de otimalidade dos preços (quanto um preço pode variar sem mudar a base);
* intervalos de viabilidade das exigências (faixa em que o preço-sombra continua válido).

Regra de Bland para entrada e saída, o que evita ciclagem em problemas degenerados (comuns em dietas).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .modelo import ProblemaPL

TOL = 1e-9


@dataclass
class LinhaPadrao:
    """Linha da forma padrão, com a origem dela no problema original."""

    nome: str
    descricao: str
    unidade: str
    origem: str          # "restricao" ou "limite_superior"
    sinal: float         # +1 se a linha manteve o sentido, -1 se foi multiplicada por -1 para ter lado direito ≥ 0
    sentido_original: str


@dataclass
class QuadroFinal:
    nomes_colunas: list[str]
    base: list[int]
    tableau: np.ndarray          # linhas = restrições da forma padrão, última coluna = lado direito
    custos_reduzidos: np.ndarray
    valor_objetivo: float
    linhas: list[LinhaPadrao]
    x: np.ndarray                # solução nas variáveis originais (com limites inferiores somados)
    duais: np.ndarray            # preço-sombra por linha da forma padrão, no sentido do problema original
    faixa_custos: pd.DataFrame
    faixa_lados_direitos: pd.DataFrame

    def como_dataframe(self) -> pd.DataFrame:
        quadro = pd.DataFrame(self.tableau[:, :-1], columns=self.nomes_colunas)
        quadro.insert(0, "base", [self.nomes_colunas[j] for j in self.base])
        quadro["valor (b)"] = self.tableau[:, -1]
        fundo = pd.DataFrame([["custo reduzido (z_j - c_j)", *(-self.custos_reduzidos), self.valor_objetivo]],
                             columns=quadro.columns)
        return pd.concat([quadro, fundo], ignore_index=True)


def _forma_padrao(problema: ProblemaPL):
    """
    min cᵀx', x' = x − lb ≥ 0. Restrições: linhas originais e x'_j ≤ ub_j − lb_j para limites finitos.
    Toda linha fica com lado direito ≥ 0; '≤' recebe folga, '≥' recebe excesso e artificial, '=' não ocorre.
    """
    lb, ub = problema.limite_inferior, problema.limite_superior
    n = len(problema.custos)
    linhas_a, rhs, sentidos, meta = [], [], [], []
    for r in problema.restricoes:
        b = r.lado_direito - r.coeficientes @ lb
        linhas_a.append(r.coeficientes.copy())
        rhs.append(b)
        sentidos.append(r.sentido)
        meta.append((r.nome, r.descricao, r.unidade, "restricao", r.sentido))
    for j in range(n):
        if np.isfinite(ub[j]):
            e = np.zeros(n)
            e[j] = 1.0
            linhas_a.append(e)
            rhs.append(ub[j] - lb[j])
            sentidos.append("<=")
            meta.append((f"max_{problema.nomes_variaveis[j]}", f"Limite máximo de {problema.nomes_variaveis[j]}",
                         "kg MS", "limite_superior", "<="))

    linhas = []
    for k in range(len(linhas_a)):
        sinal = 1.0
        if rhs[k] < 0:
            linhas_a[k], rhs[k], sinal = -linhas_a[k], -rhs[k], -1.0
            sentidos[k] = ">=" if sentidos[k] == "<=" else "<="
        nome, desc, unid, origem, sentido_original = meta[k]
        linhas.append(LinhaPadrao(nome, desc, unid, origem, sinal, sentido_original))
    return np.array(linhas_a), np.array(rhs, dtype=float), sentidos, linhas


def _pivotar(t: np.ndarray, linha: int, coluna: int) -> None:
    t[linha] /= t[linha, coluna]
    for i in range(t.shape[0]):
        if i != linha and abs(t[i, coluna]) > TOL:
            t[i] -= t[i, coluna] * t[linha]


def _simplex(t: np.ndarray, base: list[int], custo: np.ndarray, colunas_permitidas: np.ndarray,
             max_iter: int = 10_000) -> None:
    """Minimiza custoᵀx sobre o quadro ``t`` (in-place). Regra de Bland."""
    for _ in range(max_iter):
        cb = custo[base]
        reduzidos = custo - cb @ t[:, :-1]
        entra = next((j for j in range(len(custo)) if colunas_permitidas[j] and reduzidos[j] < -1e-10), None)
        if entra is None:
            return
        razoes = [(t[i, -1] / t[i, entra], base[i], i) for i in range(t.shape[0]) if t[i, entra] > 1e-10]
        if not razoes:
            raise ValueError("PL ilimitada.")
        menor = min(r[0] for r in razoes)
        _, _, sai = min((r for r in razoes if r[0] <= menor + 1e-12), key=lambda r: r[1])
        _pivotar(t, sai, entra)
        base[sai] = entra
    raise RuntimeError("Simplex não convergiu no número máximo de iterações.")


def resolver_tabular(problema: ProblemaPL) -> QuadroFinal:
    a, b, sentidos, linhas = _forma_padrao(problema)
    m, n = a.shape
    nomes = list(problema.nomes_variaveis)
    colunas = [a]
    base: list[int] = [-1] * m
    proxima = n
    artificiais = []
    for i, sentido in enumerate(sentidos):
        e = np.zeros((m, 1))
        e[i] = 1.0
        if sentido == "<=":
            colunas.append(e)
            nomes.append(f"folga_{linhas[i].nome}")
            base[i] = proxima
            proxima += 1
        else:
            colunas.append(-e)
            nomes.append(f"excesso_{linhas[i].nome}")
            proxima += 1
            colunas.append(e)
            nomes.append(f"art_{linhas[i].nome}")
            artificiais.append(proxima)
            base[i] = proxima
            proxima += 1
    matriz = np.hstack(colunas)
    t = np.hstack([matriz, b.reshape(-1, 1)])
    total = matriz.shape[1]

    # Fase 1: minimizar a soma das artificiais.
    custo_f1 = np.zeros(total)
    custo_f1[artificiais] = 1.0
    _simplex(t, base, custo_f1, np.ones(total, dtype=bool))
    if custo_f1[base] @ t[:, -1] > 1e-7:
        raise ValueError("PL inviável (fase 1 terminou com artificiais positivas).")
    # Artificiais que ficaram na base com valor zero são trocadas por qualquer coluna não artificial.
    for i, j in enumerate(base):
        if j in artificiais:
            candidata = next((k for k in range(total) if k not in artificiais and abs(t[i, k]) > 1e-9), None)
            if candidata is not None:
                _pivotar(t, i, candidata)
                base[i] = candidata

    # Fase 2: custo original, artificiais proibidas.
    custo = np.zeros(total)
    custo[:n] = problema.custos
    permitidas = np.ones(total, dtype=bool)
    permitidas[artificiais] = False
    _simplex(t, base, custo, permitidas)

    reduzidos = custo - custo[base] @ t[:, :-1]
    x_linha = np.zeros(total)
    x_linha[base] = t[:, -1]
    x = x_linha[:n] + problema.limite_inferior
    valor = float(problema.custos @ x)

    # Duais: y = c_B·B⁻¹, com B formada pelas colunas da base na matriz original.
    b_inv = np.linalg.inv(matriz[:, base])
    y_padrao = custo[base] @ b_inv
    duais = y_padrao * np.array([l.sinal for l in linhas])

    faixa_custos = _faixa_custos(problema, t, base, reduzidos, permitidas, n)
    faixa_rhs = _faixa_lados_direitos(problema, linhas, b_inv, t[:, -1], duais, b)

    return QuadroFinal(nomes, base, t, reduzidos, valor, linhas, x, duais, faixa_custos, faixa_rhs)


def _faixa_custos(problema: ProblemaPL, t, base, reduzidos, permitidas, n) -> pd.DataFrame:
    linhas = []
    nao_basicas = [k for k in range(len(reduzidos)) if k not in base and permitidas[k]]
    for j in range(n - 1):  # a variável auxiliar 'm' tem custo zero e não é ingrediente
        c = problema.custos[j]
        if j in base:
            r = base.index(j)
            sobe, desce = np.inf, np.inf
            for k in nao_basicas:
                alfa = t[r, k]
                if alfa > 1e-10:
                    sobe = min(sobe, reduzidos[k] / alfa)
                elif alfa < -1e-10:
                    desce = min(desce, -reduzidos[k] / alfa)
            linhas.append((problema.nomes_variaveis[j], "básica", c, c - desce, c + sobe, 0.0))
        else:
            d = reduzidos[j]
            # Não básica no limite inferior: entra se o preço cair mais que o custo reduzido.
            # Não básica no limite superior (folga do limite na base fica zero) aparece como básica acima.
            linhas.append((problema.nomes_variaveis[j], "não básica", c, c - d, np.inf, d))
    return pd.DataFrame(linhas, columns=["rufas_id", "situacao", "preco_atual", "preco_min", "preco_max",
                                         "custo_reduzido"])


def _faixa_lados_direitos(problema: ProblemaPL, linhas, b_inv, x_base, duais, b_padrao) -> pd.DataFrame:
    registros = []
    for i, linha in enumerate(linhas):
        coluna = b_inv[:, i]
        sobe, desce = np.inf, np.inf
        for valor, a in zip(x_base, coluna):
            if a > 1e-10:
                desce = min(desce, valor / a)
            elif a < -1e-10:
                sobe = min(sobe, -valor / a)
        # O passo acima é em b da forma padrão; converte para o lado direito original.
        if linha.origem == "restricao":
            rhs_original = next(r.lado_direito for r in problema.restricoes if r.nome == linha.nome)
        else:
            j = problema.nomes_variaveis.index(linha.nome.removeprefix("max_"))
            rhs_original = problema.limite_superior[j]
        if linha.sinal > 0:
            minimo, maximo = rhs_original - desce, rhs_original + sobe
        else:
            minimo, maximo = rhs_original - sobe, rhs_original + desce
        registros.append((linha.nome, linha.descricao, linha.unidade, linha.sentido_original, rhs_original,
                          duais[i], minimo, maximo))
    return pd.DataFrame(registros, columns=["restricao", "descricao", "unidade", "sentido", "lado_direito",
                                            "preco_sombra", "valido_de", "valido_ate"])
