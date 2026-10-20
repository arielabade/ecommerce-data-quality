"""Detectores de outlier: IQR, z-score, z robusto (MAD), STL e Isolation Forest.

Papel: funções puras que recebem números e devolvem uma máscara booleana
"suspeito". Não decidem o que fazer; quem decide é o pipeline (que só
*sinaliza* outliers estatísticos, ver ``docs/DECISIONS.md``) e a avaliação
(que mede o que cada detector acerta e erra contra o registro de defeitos).
Entrada: ``pd.Series`` ou ``DataFrame`` numérico.
Saída: ``pd.Series[bool]`` alinhada ao índice da entrada.
Dependências: numpy, pandas, statsmodels (STL), scikit-learn (Isolation Forest).

Quando cada um serve:
- **IQR** (Q1 - k*IQR, Q3 + k*IQR): simples e sem suposição de forma, mas numa
  distribuição assimétrica (ticket de e-commerce) a cerca de cima corta a cauda
  legítima inteira.
- **z-score** (média/desvio): assume normal e é contaminado pelos próprios
  outliers; incluído para mostrar por que não usar.
- **z robusto** (mediana/MAD, corte 3,5): resiste a até 50% de contaminação;
  aplicado *por grupo* (ex.: por tipo de cliente) compara cada valor com seus
  pares, e um pedido de confecção deixa de ser comparado com o de quem costura
  por hobby.
- **STL**: separa tendência e sazonalidade semanal de uma série diária; o
  outlier é o resíduo grande. Sozinho, ainda marca a Black Friday (é um pico
  anual, não semanal); com o calendário comercial, o pico explicado por evento
  deixa de ser alarme.
- **Isolation Forest**: combinações estranhas em várias colunas ao mesmo tempo
  (frete normal e peso normal, mas aquele frete para aquele peso não existe).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

MAD_K = 1.4826  # torna o MAD comparável ao desvio padrão numa normal


def iqr_flags(x: pd.Series, k: float = 1.5) -> pd.Series:
    q1, q3 = x.quantile([0.25, 0.75])
    iqr = q3 - q1
    return ((x < q1 - k * iqr) | (x > q3 + k * iqr)).fillna(False)


def zscore_flags(x: pd.Series, thr: float = 3.0) -> pd.Series:
    sd = x.std()
    return ((x - x.mean()).abs() / (sd if sd else 1) > thr).fillna(False)


def robust_z(x: pd.Series) -> pd.Series:
    med = x.median()
    mad = MAD_K * (x - med).abs().median()
    if not mad or np.isnan(mad):
        mad = 1.253314 * (x - med).abs().mean() or 1.0  # MAD zero (muitos empates): cai para o desvio médio absoluto
    return (x - med) / mad


def mad_flags(x: pd.Series, thr: float = 3.5) -> pd.Series:
    return (robust_z(x).abs() > thr).fillna(False)


def grouped(fn, x: pd.Series, groups: pd.Series, **kw) -> pd.Series:
    """Aplica um detector dentro de cada grupo (pares comparados com pares)."""
    out = pd.Series(False, index=x.index)
    for _, idx in x.groupby(groups, dropna=False).groups.items():
        if len(idx) >= 8:
            out.loc[idx] = fn(x.loc[idx], **kw)
    return out


def grouped_robust_z(x: pd.Series, groups: pd.Series) -> pd.Series:
    out = pd.Series(np.nan, index=x.index)
    for _, idx in x.groupby(groups, dropna=False).groups.items():
        if len(idx) >= 8:
            out.loc[idx] = robust_z(x.loc[idx])
    return out


def stl_residual_z(series: pd.Series, period: int = 7) -> pd.Series:
    """z robusto do resíduo de uma decomposição STL robusta (série diária completa)."""
    from statsmodels.tsa.seasonal import STL

    s = series.asfreq("D").fillna(series.median())
    res = STL(np.log1p(s.clip(lower=0)), period=period, robust=True).fit()
    return robust_z(pd.Series(res.resid, index=s.index)).reindex(series.index)


def stl_flags(series: pd.Series, period: int = 7, thr: float = 4.0, upper_only: bool = True) -> pd.Series:
    z = stl_residual_z(series, period)
    return (z > thr) if upper_only else (z.abs() > thr)


def isolation_forest_flags(X: pd.DataFrame, contamination: float = 0.01, seed: int = 0) -> tuple[pd.Series, pd.Series]:
    """(máscara, score). Score maior = mais isolado. Semente fixa: resultado reprodutível."""
    from sklearn.ensemble import IsolationForest

    Xf = X.astype(float).fillna(X.astype(float).median())
    model = IsolationForest(n_estimators=200, contamination=contamination, random_state=seed, n_jobs=1)
    pred = model.fit_predict(Xf)
    score = -model.score_samples(Xf)
    return pd.Series(pred == -1, index=X.index), pd.Series(score, index=X.index)


def winsorize(x: pd.Series, q: float = 0.995, groups: pd.Series | None = None) -> pd.Series:
    """Limita cada valor ao quantil ``q`` (do grupo, se dado). Só para features de modelo."""
    if groups is None:
        return x.clip(upper=x.quantile(q))
    cap = x.groupby(groups).transform(lambda s: s.quantile(q))
    return x.where(x <= cap, cap)
