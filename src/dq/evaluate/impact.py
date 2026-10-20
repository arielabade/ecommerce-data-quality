"""Impacto da limpeza nas métricas de negócio, e o custo de uma decisão errada.

Papel: rodar os mesmos marts sobre três versões da base e comparar:
- **verdade** (gold: o que realmente aconteceu);
- **antes** (staging: a base suja, só tipada);
- **depois** (clean: o resultado do ETL).
Também calcula o exemplo de decisão errada (IQR global apagando a Black
Friday) e o efeito de cada escala de normalização para modelagem.
Entrada: warehouse (``staging`` e ``clean``), ``Truth``.
Saída: DataFrames com métricas por versão, por mês e por região.
Dependências: duckdb, ``dq.load.marts``, scikit-learn (Yeo-Johnson).
"""

from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd
from scipy import stats

from dq.clean import outliers
from dq.clean.staging import local_date
from dq.evaluate.truth import Truth
from dq.load import marts

VERSIONS = {"verdade": "gold", "antes": "wh.staging", "depois": "wh.clean"}


def eval_connection(warehouse_path, truth: Truth) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(config={"TimeZone": "UTC"})
    con.execute(f"ATTACH '{warehouse_path}' AS wh (READ_ONLY)")
    con.execute("CREATE SCHEMA gold")
    for name, df in truth.gold.items():
        df = df.copy()
        if name == "pedidos":
            df["data_local"] = local_date(df["criado_em"])
        con.register("_g", df)
        con.execute(f"CREATE TABLE gold.{name} AS SELECT * FROM _g")
        con.unregister("_g")
    legit = truth.log[truth.log["legitimo"] & (truth.log["tabela"] == "pedidos")]
    leg = pd.DataFrame({"pedido_id": legit["chave"].astype("int64"), "evento": legit["tipo_defeito"].str.replace("legitimo_", "")})
    con.register("_l", leg.drop_duplicates("pedido_id"))
    con.execute("CREATE TABLE legit AS SELECT * FROM _l")
    con.unregister("_l")
    for v, src in VERSIONS.items():
        marts.build(con, src, f"m_{v}")
    return con


def headline(con) -> pd.DataFrame:
    rows = []
    for v in VERSIONS:
        r = marts.summary(con, f"m_{v}").iloc[0].to_dict()
        r["versao"] = v
        rows.append(r)
    df = pd.DataFrame(rows).set_index("versao")
    cats = {v: con.execute(f"SELECT count(DISTINCT categoria) FROM m_{v}.receita_categoria_mensal").fetchone()[0]
            for v in VERSIONS}
    df["categorias_distintas"] = pd.Series(cats)
    return df


def monthly(con) -> pd.DataFrame:
    parts = []
    for v in VERSIONS:
        d = con.execute(f"""SELECT mes, sum(pedidos) pedidos, sum(pedidos_pagos) pedidos_pagos, sum(cancelados) cancelados,
                                   sum(receita_centavos)/100.0 receita, sum(pedidos_recorrentes) recorrentes
                            FROM m_{v}.metricas_mensais GROUP BY mes ORDER BY mes""").df()
        d["versao"] = v
        parts.append(d)
    d = pd.concat(parts)
    d["ticket_medio"] = d["receita"] / d["pedidos_pagos"]
    d["taxa_cancelamento"] = d["cancelados"] / d["pedidos"]
    d["recompra_pct"] = d["recorrentes"] / d["pedidos_pagos"]
    d["mes"] = pd.to_datetime(d["mes"]).dt.strftime("%Y-%m")
    return d[(d["mes"] >= "2023-10") & (d["mes"] <= "2026-10")]


def regional(con) -> pd.DataFrame:
    parts = []
    for v in VERSIONS:
        d = con.execute(f"""SELECT regiao, sum(pedidos) pedidos, sum(pedidos_pagos) pedidos_pagos, sum(cancelados) cancelados,
                                   sum(receita_centavos)/100.0 receita, sum(pedidos_recorrentes) recorrentes
                            FROM m_{v}.metricas_mensais GROUP BY regiao""").df()
        d["versao"] = v
        parts.append(d)
    d = pd.concat(parts)
    d["ticket_medio"] = d["receita"] / d["pedidos_pagos"]
    d["taxa_cancelamento"] = d["cancelados"] / d["pedidos"]
    return d


def category(con) -> pd.DataFrame:
    parts = []
    for v in VERSIONS:
        d = con.execute(f"SELECT categoria, sum(receita_itens_centavos)/100.0 receita FROM m_{v}.receita_categoria_mensal GROUP BY 1").df()
        d["versao"] = v
        parts.append(d)
    return pd.concat(parts)


def wrong_decision(con) -> dict:
    """O que acontece se "limpar outliers" com IQR global sobre a base já limpa.

    Duas versões do mesmo erro: (a) apagar pedidos com ticket acima da cerca do
    IQR; (b) limitar (winsorizar) a receita diária na cerca do IQR. As duas
    tratam o pico da Black Friday e os pedidos B2B como erro.
    """
    p = con.execute("""SELECT pedido_id, total_centavos/100.0 total, data_local, status
                       FROM wh.clean.pedidos WHERE status IN ('pago','enviado','entregue','devolvido')""").df()
    gold = con.execute("""SELECT date_trunc('month', data_local)::DATE mes, sum(receita_centavos)/100.0 receita
                          FROM m_verdade.vendas_diarias GROUP BY 1""").df()
    gold["mes"] = pd.to_datetime(gold["mes"]).dt.strftime("%Y-%m")
    gold = gold.set_index("mes")["receita"]
    p["mes"] = pd.to_datetime(p["data_local"]).dt.strftime("%Y-%m")
    out: dict = {}
    fence = p["total"].quantile(0.75) + 1.5 * (p["total"].quantile(0.75) - p["total"].quantile(0.25))
    keep = p["total"] <= fence
    by_month_rm = p[keep].groupby("mes")["total"].sum()
    by_month_all = p.groupby("mes")["total"].sum()
    out["remover_pedidos_iqr"] = {
        "cerca_reais": float(fence), "pedidos_removidos": int((~keep).sum()),
        "pedidos_removidos_pct": float((~keep).mean()),
        "receita_removida": float(p.loc[~keep, "total"].sum()),
        "receita_removida_pct": float(p.loc[~keep, "total"].sum() / p["total"].sum()),
        "por_mes": [{"mes": m, "verdade": float(gold.get(m, np.nan)), "limpo": float(by_month_all.get(m, 0)),
                     "decisao_errada": float(by_month_rm.get(m, 0))} for m in sorted(by_month_all.index)],
    }
    d = p.groupby("data_local")["total"].sum()
    q1, q3 = d.quantile([0.25, 0.75])
    cap = q3 + 1.5 * (q3 - q1)
    w = d.clip(upper=cap)
    dm = pd.Series(pd.to_datetime(d.index).strftime("%Y-%m"), index=d.index)
    by_m = w.groupby(dm.to_numpy()).sum()
    raw_m = d.groupby(dm.to_numpy()).sum()
    capped_days = d[d > cap]
    out["winsorizar_dia_iqr"] = {
        "teto_diario_reais": float(cap), "dias_limitados": int(len(capped_days)),
        "dias": [str(x) for x in capped_days.index],
        "por_mes": [{"mes": m, "verdade": float(gold.get(m, np.nan)), "limpo": float(raw_m.get(m, 0)),
                     "decisao_errada": float(by_m.get(m, 0))} for m in sorted(raw_m.index)],
    }
    # (c) apagar os dias "anômalos" pela cerca do IQR antes de somar o mês (comum em previsão de demanda)
    kept = d[d <= cap]
    by_m_drop = kept.groupby(dm[kept.index].to_numpy()).sum()
    out["remover_dias_iqr"] = {
        "dias_removidos": int((d > cap).sum()),
        "por_mes": [{"mes": m, "verdade": float(gold.get(m, np.nan)), "limpo": float(raw_m.get(m, 0)),
                     "decisao_errada": float(by_m_drop.get(m, 0))} for m in sorted(raw_m.index)],
    }
    # O que cada decisão faz com os eventos legítimos
    leg = con.execute("""SELECT o.pedido_id, CASE WHEN l.pedido_id IS NOT NULL THEN l.evento END AS evento
                         FROM wh.clean.pedidos o LEFT JOIN legit l USING (pedido_id)""").df() if _has(con, "legit") else None
    if leg is not None:
        p2 = p.merge(leg, on="pedido_id", how="left")
        ev = {}
        for e, g in p2[p2["evento"].notna()].groupby("evento"):
            day_capped = g.groupby("data_local")["total"].sum()
            ev[e] = {"receita": float(g["total"].sum()),
                     "removida_pedidos_iqr": float(g.loc[g["total"] > fence, "total"].sum() / g["total"].sum()),
                     "removida_dias_iqr": float(day_capped[day_capped.index.isin(d[d > cap].index)].sum() / g["total"].sum())}
        out["eventos_legitimos"] = ev
    for k in ("remover_pedidos_iqr", "winsorizar_dia_iqr", "remover_dias_iqr"):
        pm = pd.DataFrame(out[k]["por_mes"]).set_index("mes")
        pm["erro_pct"] = pm["decisao_errada"] / pm["limpo"] - 1  # efeito só da decisão, sobre a base já limpa
        nov = [m for m in pm.index if m.endswith("-11")]
        out[k]["novembros"] = {m: float(pm.loc[m, "erro_pct"]) for m in nov}
        out[k]["mes_mais_afetado"] = str(pm["erro_pct"].idxmin())
        out[k]["erro_mes_mais_afetado"] = float(pm["erro_pct"].min())
        out[k]["erro_medio_outros_meses"] = float(pm.loc[[m for m in pm.index if not m.endswith("-11")], "erro_pct"].mean())
    return out


def _has(con, name: str) -> bool:
    return bool(con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name=?", [name]).fetchone()[0])


def scaling(con) -> tuple[pd.DataFrame, dict]:
    """Ticket por pedido (base limpa) em cinco escalas: quando cada uma distorce ou ajuda."""
    from sklearn.preprocessing import PowerTransformer

    x = con.execute("""SELECT total_centavos/100.0 t FROM wh.clean.pedidos
                       WHERE status IN ('pago','enviado','entregue','devolvido')""").df()["t"].to_numpy()
    q1, med, q3 = np.percentile(x, [25, 50, 75])
    variants = {
        "original (R$)": x,
        "min-max": (x - x.min()) / (x.max() - x.min()),
        "z-score": (x - x.mean()) / x.std(),
        "escala robusta (mediana/IQR)": (x - med) / (q3 - q1),
        "log(1 + x)": np.log1p(x),
        "Yeo-Johnson": PowerTransformer(method="yeo-johnson").fit_transform(x.reshape(-1, 1)).ravel(),
    }
    rows = []
    hist = {}
    for k, v in variants.items():
        lo, hi = np.min(v), np.max(v)
        rng = hi - lo
        rows.append({
            "escala": k, "assimetria": float(stats.skew(v)), "mediana": float(np.median(v)), "max": float(hi),
            "pct_no_primeiro_decimo_da_faixa": float(np.mean(v <= lo + 0.1 * rng)),
            "outliers_por_z_robusto_3_5": float(np.mean(np.abs(outliers.robust_z(pd.Series(v))) > 3.5)),
        })
        counts, edges = np.histogram(v, bins=40)
        hist[k] = {"counts": counts.tolist(), "edges": [float(e) for e in edges]}
    return pd.DataFrame(rows), hist
