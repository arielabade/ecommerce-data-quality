"""Frete: conciliação entre fontes e plausibilidade pelo peso.

Papel: decidir o frete de cada pedido quando o valor do pedido e o da
transportadora discordam, ou quando os dois concordam num valor impossível.
Entrada: pedidos e fretes de staging (com peso, serviço, valor cobrado).
Saída: ``frete_centavos`` (pedido) e ``valor_cobrado_centavos`` (frete)
conciliados + ``TreatmentLog``.
Dependências: statsmodels (regressão robusta), pandas, numpy.

Duas fontes, duas perguntas:
1. **Conciliação:** ``pedidos.frete`` deve ser igual a ``fretes.valor_cobrado``
   (100% na origem). Se divergem, vale o que for plausível para o peso.
2. **Plausibilidade:** mesmo quando concordam, o valor pode ser impossível
   (a cotação já nasceu errada, ou o frete de um pedido leve foi parar num
   pesado). Modelo: ``log(frete) = a + b*log(peso) + efeito da UF`` por
   serviço, ajustado com regressão robusta (Huber). Resíduo dividido pelo MAD
   (com piso de 5%) = z robusto.

Por que regressão e não "mediana do frete por faixa de peso": o cliente B2B
compra 200+ kg, fora de qualquer faixa observada. A faixa mais pesada teria
mediana de 15 kg e marcaria todo pedido B2B como absurdo. A reta em log
extrapola a relação peso-preço e deixa o B2B passar.

Por que MAD e não desvio padrão: o desvio padrão é inflado pelos próprios
erros que se quer achar (um frete de R$ 9.999,99 sozinho dobra o desvio de
milhares de linhas). A mediana dos desvios absolutos ignora até 50% de lixo.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import statsmodels.api as sm

from dq.clean.audit import TreatmentLog

MAD_FLOOR = 0.05  # 5%: abaixo disso, arredondamento de tarifa vira "outlier"


def _design(peso: pd.Series, uf: pd.Series, ufs: list[str]) -> pd.DataFrame:
    X = pd.DataFrame({"const": 1.0, "log_peso": np.log(peso.astype(float).where(peso > 0))}, index=peso.index)
    for u in ufs[1:]:
        X[f"uf_{u}"] = (uf == u).astype(float)
    return X


def fit_freight_model(servico: pd.Series, peso: pd.Series, uf: pd.Series, frete: pd.Series) -> dict:
    """``log(frete) = a + b*log(peso) + efeito da UF`` por serviço, com regressão robusta (Huber).

    A UF entra porque o destino explica a maior parte do preço: sem ela o
    resíduo típico é de 25%; com ela, 3%. Um frete trocado entre um pedido de
    Roraima e um de São Paulo só fica visível quando o modelo sabe a UF.
    """
    d = pd.DataFrame({"servico": servico, "peso": peso, "uf": uf.fillna("NA"), "frete": frete})
    d = d[(d["frete"] > 0) & (d["peso"] > 0)].dropna(subset=["servico", "peso", "frete"])
    models = {}
    for serv, g in d.groupby("servico"):
        if len(g) < 30:
            continue
        ufs = sorted(g["uf"].unique())
        X = _design(g["peso"], g["uf"], ufs)
        y = np.log(g["frete"].astype(float) / 100)
        res = sm.RLM(y, X, M=sm.robust.norms.HuberT()).fit()
        resid = y - res.predict(X)
        mad = 1.4826 * np.median(np.abs(resid - np.median(resid)))
        models[serv] = {"params": res.params, "ufs": ufs, "mad": float(max(mad, MAD_FLOOR))}
    return models


def robust_z(models: dict, servico: pd.Series, peso: pd.Series, uf: pd.Series, frete: pd.Series) -> tuple[pd.Series, pd.Series]:
    """(z robusto, frete esperado em centavos). NaN onde não há modelo ou frete <= 0."""
    z = pd.Series(np.nan, index=frete.index)
    expected = pd.Series(np.nan, index=frete.index)
    uf = uf.fillna("NA")
    for serv, m in models.items():
        idx = servico.index[(servico == serv).fillna(False).to_numpy()]
        if not len(idx):
            continue
        X = _design(peso.loc[idx], uf.loc[idx], m["ufs"])[m["params"].index]
        pred = X.to_numpy() @ m["params"].to_numpy()
        f = frete.loc[idx].astype(float)
        z.loc[idx] = (np.log((f / 100).where(f > 0)) - pred) / m["mad"]
        expected.loc[idx] = np.round(np.exp(pred) * 100)
    return z, expected


def order_uf(ped: pd.DataFrame, enderecos: pd.DataFrame) -> pd.Series:
    e = enderecos.drop_duplicates("endereco_id").set_index("endereco_id")["uf"]
    return ped["endereco_entrega_id"].map(e)


def reconcile_freight(ped: pd.DataFrame, fr: pd.DataFrame, enderecos: pd.DataFrame, cfg: dict,
                      log: TreatmentLog) -> tuple[pd.DataFrame, pd.DataFrame]:
    ped, fr = ped.copy(), fr.copy()
    thr = cfg["mad_z_max"]
    f1 = fr.drop_duplicates("pedido_id").set_index("pedido_id")
    serv = ped["pedido_id"].map(f1["servico"])
    peso = ped["pedido_id"].map(f1["peso_kg"])
    cobrado = ped["pedido_id"].map(f1["valor_cobrado_centavos"])
    frete = ped["frete_centavos"]
    uf = order_uf(ped, enderecos)
    models = fit_freight_model(serv, peso, uf, cobrado)
    z_p, expected = robust_z(models, serv, peso, uf, frete)
    z_c, _ = robust_z(models, serv, peso, uf, cobrado)
    ok_p, ok_c = z_p.abs() <= thr, z_c.abs() <= thr
    k = ped["pedido_id"]

    diverge = cobrado.notna() & frete.ne(cobrado).fillna(False)
    take_c = diverge & (ok_c | cobrado.eq(0)) & ~ok_p.fillna(False)
    log.add_many("pedidos", k[take_c], "frete", "frete_conciliado_transportadora", "corrigido", frete[take_c], cobrado[take_c])
    ped.loc[take_c, "frete_centavos"] = cobrado[take_c]
    take_p = diverge & ok_p.fillna(False) & ~ok_c.fillna(False)
    log.add_many("fretes", k[take_p], "valor_cobrado", "frete_conciliado_pedido", "corrigido", cobrado[take_p], frete[take_p])
    both_bad = (frete > 0) & ~ok_p.fillna(True) & ~(take_c | take_p) & expected.notna()
    log.add_many("pedidos", k[both_bad], "frete", "frete_implausivel_para_o_peso", "imputado",
                 frete[both_bad], expected[both_bad].astype("Int64"))
    ped.loc[both_bad, "frete_centavos"] = expected[both_bad].astype("Int64")

    final = ped.set_index("pedido_id")["frete_centavos"]
    final = final[~final.index.duplicated()]
    new_c = fr["pedido_id"].map(final)
    upd = new_c.notna() & new_c.ne(fr["valor_cobrado_centavos"]).fillna(True)
    fr.loc[upd, "valor_cobrado_centavos"] = new_c[upd].astype("Int64")
    return ped, fr
