"""Nulos: excluir, mediana por grupo ou modelo — qual erra menos e onde.

Papel: comparar os três tratamentos para ``fretes.custo_real`` contra o valor
verdadeiro, separando por mecanismo de ausência (MCAR, MAR, MNAR), e medir o
viés no número que o negócio usa (custo total de frete).
Entrada: fretes de staging (com os nulos), ``Truth``.
Saída: DataFrame por método x mecanismo com erro absoluto médio e viés.
Dependências: ``dq.clean.nulls``.
"""

from __future__ import annotations

import pandas as pd

from dq.clean.nulls import impute
from dq.evaluate.truth import Truth


def compare(stg_fretes: pd.DataFrame, truth: Truth) -> pd.DataFrame:
    fr = stg_fretes.drop_duplicates("frete_id").reset_index(drop=True)
    fr = fr[fr["transportadora"] != "Retirada na loja"].reset_index(drop=True)
    gold = truth.gold["fretes"].set_index("frete_id")["custo_real_centavos"].astype(float)
    true = fr["frete_id"].map(gold)
    mech = {}
    for m in ("mcar", "mar", "mnar"):
        for k in truth.keys("fretes", f"nulo_{m}"):
            mech[int(k)] = m.upper()
    fr["mecanismo"] = fr["frete_id"].map(mech)
    miss = fr["custo_real_centavos"].isna() & fr["mecanismo"].notna()
    rows = []
    total_true = true.sum()
    for method in ("exclusao", "mediana_grupo", "modelo"):
        est = impute(fr, method).astype(float)
        for m in ("MCAR", "MAR", "MNAR", "todos"):
            sel = miss & ((fr["mecanismo"] == m) if m != "todos" else True)
            if method == "exclusao":
                mae = float("nan")
                bias = float("nan")
            else:
                err = (est[sel] - true[sel]) / 100
                mae, bias = float(err.abs().mean()), float(err.mean())
            rows.append({"metodo": method, "mecanismo": m, "linhas": int(sel.sum()), "erro_abs_medio_reais": mae,
                         "vies_medio_reais": bias})
        # Número de negócio: custo médio de frete por envio (excluir = média só dos observados)
        if method == "exclusao":
            mean_est = est[~miss].mean()
        else:
            mean_est = est.mean()
        rows.append({"metodo": method, "mecanismo": "custo médio por envio", "linhas": int(len(fr)),
                     "erro_abs_medio_reais": float(mean_est - true.mean()) / 100,
                     "vies_medio_reais": float((mean_est - true.mean()) / true.mean())})
    out = pd.DataFrame(rows)
    out.attrs["custo_total_verdadeiro"] = float(total_true) / 100
    return out
