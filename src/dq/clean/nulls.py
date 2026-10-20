"""Nulos: entender o mecanismo antes de imputar.

Papel: analisar por que ``fretes.custo_real`` (custo que a transportadora
cobra da loja, base da margem de frete) está faltando e preencher com o
método configurado, marcando cada valor imputado.
Entrada: fretes de staging.
Saída: ``custo_real_centavos`` imputado + coluna ``custo_real_imputado`` +
diagnóstico do mecanismo (dicionário) + ``TreatmentLog``.
Dependências: scipy, scikit-learn, pandas, numpy.

Os três mecanismos (Rubin):
- **MCAR**, ausente ao acaso: a falta não depende de nada. Excluir não enviesa,
  só perde amostra.
- **MAR**, depende de outra coluna observada (uma transportadora não devolve o
  custo): excluir enviesa; condicionar na coluna (mediana por grupo, modelo)
  corrige.
- **MNAR**, depende do próprio valor (os fretes mais caros são os que somem,
  porque a fatura é contestada): nenhum método só com dados observados corrige
  por completo. Um proxy forte do valor (aqui, o valor cobrado do cliente e o
  peso) reduz o viés; excluir o aumenta.

Diagnóstico: compara a taxa de ausência entre grupos (qui-quadrado) e a
distribuição de um proxy entre linhas com e sem valor (Kolmogorov-Smirnov).
Ausência que varia por transportadora = indício de MAR; linhas ausentes com
peso e valor cobrado maiores = indício de MNAR (ou de MAR via o proxy; dados
observados não distinguem os dois, e o documento diz isso).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from dq.clean.audit import TreatmentLog

FEATURES = ["peso_kg", "valor_cobrado_centavos", "frete_gratis"]
CATS = ["transportadora", "servico"]


def diagnose(fr: pd.DataFrame, col: str = "custo_real_centavos") -> dict:
    d = fr[fr["transportadora"] != "Retirada na loja"]
    miss = d[col].isna()
    out = {"coluna": col, "linhas": int(len(d)), "ausentes": int(miss.sum()), "ausentes_pct": float(miss.mean() * 100)}
    tab = pd.crosstab(d["transportadora"], miss)
    chi2, pval, *_ = stats.chi2_contingency(tab)
    out["por_transportadora_pct"] = (miss.groupby(d["transportadora"]).mean() * 100).round(2).to_dict()
    out["qui2_transportadora"] = {"estatistica": float(chi2), "p_valor": float(pval)}
    for proxy in ("peso_kg", "valor_cobrado_centavos"):
        a, b = d.loc[miss, proxy].dropna(), d.loc[~miss, proxy].dropna()
        ks = stats.ks_2samp(a, b)
        out[f"ks_{proxy}"] = {"mediana_ausentes": float(a.median()), "mediana_presentes": float(b.median()),
                              "estatistica": float(ks.statistic), "p_valor": float(ks.pvalue)}
    mcar_like = out["qui2_transportadora"]["p_valor"] > 0.01 and out["ks_peso_kg"]["p_valor"] > 0.01
    out["leitura"] = (
        "compatível com MCAR" if mcar_like else
        "não é MCAR: a ausência depende da transportadora (MAR) e é maior nos fretes mais pesados/caros "
        "(MNAR ou MAR via peso; dados observados não separam os dois)"
    )
    return out


def _design(fr: pd.DataFrame, cats: list[str] | None = None) -> pd.DataFrame:
    X = fr[FEATURES].astype(float).copy()
    X["log_peso"] = np.log1p(X["peso_kg"])
    X["log_cobrado"] = np.log1p(X["valor_cobrado_centavos"].clip(lower=0))
    X = pd.concat([X, pd.get_dummies(fr[CATS].astype(str), dtype=float)], axis=1)
    if cats is not None:
        X = X.reindex(columns=cats, fill_value=0.0)
    return X


def impute(fr: pd.DataFrame, method: str, col: str = "custo_real_centavos", seed: int = 0) -> pd.Series:
    """Valores para as linhas nulas segundo o método. ``exclusao`` devolve NaN (a linha sai da conta)."""
    miss = fr[col].isna() & (fr["transportadora"] != "Retirada na loja")
    out = fr[col].astype("Float64").copy()
    if method == "exclusao":
        return out
    if method == "mediana_grupo":
        # razão custo/peso por serviço e faixa de peso: custo escala com o peso dentro do serviço
        bins = pd.qcut(fr["peso_kg"].rank(method="first"), 10, labels=False)
        rate = (fr[col] / fr["peso_kg"].clip(lower=0.05)).astype(float)
        med = rate.groupby([fr["servico"], bins]).transform("median")
        med = med.fillna(rate.groupby(fr["servico"]).transform("median")).fillna(rate.median())
        out[miss] = (med[miss] * fr.loc[miss, "peso_kg"].clip(lower=0.05)).round()
        return out
    if method == "modelo":
        from sklearn.ensemble import HistGradientBoostingRegressor

        train = fr[col].notna() & (fr["transportadora"] != "Retirada na loja")
        X = _design(fr)
        model = HistGradientBoostingRegressor(max_iter=300, learning_rate=0.08, random_state=seed)
        model.fit(X[train], np.log1p(fr.loc[train, col].astype(float)))
        if miss.any():
            out[miss] = np.expm1(model.predict(X[miss])).round()
        return out
    raise ValueError(f"método de imputação desconhecido: {method}")


def impute_freight_cost(fr: pd.DataFrame, method: str, log: TreatmentLog) -> tuple[pd.DataFrame, dict]:
    fr = fr.copy()
    diag = diagnose(fr)
    miss = fr["custo_real_centavos"].isna() & (fr["transportadora"] != "Retirada na loja")
    new = impute(fr, method)
    fr["custo_real_imputado"] = miss & new.notna()
    if method != "exclusao":
        fr.loc[miss, "custo_real_centavos"] = new[miss].round().astype("Int64")
        log.add_many("fretes", fr.loc[miss, "frete_id"], "custo_real", f"custo_real_{method}", "imputado",
                     None, fr.loc[miss, "custo_real_centavos"])
    return fr, diag
