"""Precisão e recall de cada técnica de detecção, contra o registro de defeitos.

Papel: rodar cada técnica isoladamente sobre a base como chega (staging) e
medir o que ela pega, o que ela erra e quantos outliers legítimos derruba.
Entrada: tabelas de staging (do warehouse), ``Truth``, ``config/cleaning.yaml``.
Saída: tabelas de resultado por unidade de análise:
- valores de pedido (frete, desconto, total);
- preços de item;
- série diária de receita;
- duplicatas;
- pedidos que não são vendas (teste e bot).
Dependências: ``dq.clean.outliers``, ``dq.clean.duplicates``, ``dq.clean.suspicious``, ``dq.clean.freight``.

Definições: precisão = acertos / sinalizados; recall = acertos / erros
existentes; "legítimos derrubados" = outliers verdadeiros (Black Friday, B2B,
viral) que a técnica marcou como erro. Uma técnica só é útil se os três
números forem bons ao mesmo tempo.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from dq.clean import duplicates, freight, outliers, suspicious, text
from dq.clean.audit import TreatmentLog
from dq.evaluate.truth import ITEM_VALUE_ERRORS, NOT_SALES, ORDER_VALUE_ERRORS, Truth

PAID = ["pago", "enviado", "entregue", "devolvido"]


def score(name: str, flagged: set, errors: set, legit: set, population: set, value: pd.Series | None = None,
          familia: str = "", nota: str = "", distortion: pd.Series | None = None) -> dict:
    """``value``: receita de cada registro (custo de derrubar um legítimo).
    ``distortion``: |valor sujo - valor verdadeiro| (custo de deixar passar um erro)."""
    flagged = flagged & population
    errors = errors & population
    legit = legit & population
    tp = flagged & errors
    lh = flagged & legit
    out = {
        "familia": familia, "tecnica": name, "sinalizados": len(flagged), "acertos": len(tp),
        "erros_existentes": len(errors),
        "precisao": len(tp) / len(flagged) if flagged else float("nan"),
        "recall": len(tp) / len(errors) if errors else float("nan"),
        "legitimos_derrubados": len(lh), "legitimos_total": len(legit),
        "legitimos_derrubados_pct": len(lh) / len(legit) if legit else float("nan"),
        "outros_sinalizados": len(flagged - errors - legit), "nota": nota,
    }
    if value is not None:
        out["valor_legitimo_derrubado"] = float(value.reindex(list(lh)).fillna(0).sum()) / 100
        d = distortion if distortion is not None else value
        out["distorcao_nao_detectada"] = float(d.reindex(list(errors - flagged)).fillna(0).abs().sum()) / 100
        out["distorcao_detectada"] = float(d.reindex(list(tp)).fillna(0).abs().sum()) / 100
    return out


def _orders_frame(stg: dict[str, pd.DataFrame], truth: Truth) -> pd.DataFrame:
    p = stg["pedidos"].drop_duplicates("pedido_id").copy()
    p = p[~p["pedido_id"].isin(truth.orders(NOT_SALES))]
    f = stg["fretes"].drop_duplicates("pedido_id").set_index("pedido_id")
    for c in ("peso_kg", "servico", "valor_cobrado_centavos"):
        p[c] = p["pedido_id"].map(f[c])
    cli = stg["clientes"].drop_duplicates("cliente_id").set_index("cliente_id")
    p["tipo_cliente"] = p["cliente_id"].map(cli["tipo_cliente"]).fillna("desconhecido")
    n_items = stg["itens_pedido"].drop_duplicates("item_pedido_id").groupby("pedido_id").size()
    p["n_itens"] = p["pedido_id"].map(n_items).fillna(0)
    return p.set_index("pedido_id", drop=False)


def order_values(stg: dict[str, pd.DataFrame], truth: Truth, cfg: dict) -> pd.DataFrame:
    """Frete, desconto e total: erros de valor no nível do pedido."""
    p = _orders_frame(stg, truth)
    pop = set(p.index)
    err = truth.orders(ORDER_VALUE_ERRORS)
    legit = truth.legit_orders
    val = p["total_centavos"].astype(float)
    gold_total = truth.gold["pedidos"].set_index("pedido_id")["total_centavos"].astype(float)
    dist = (val - p.index.to_series().map(gold_total)).abs()
    cols = ["total_centavos", "frete_centavos", "desconto_centavos"]
    X = p[cols].astype(float)
    oc = cfg["outliers"]

    def any_flag(fn, **kw):
        m = pd.Series(False, index=p.index)
        for c in cols:
            m |= fn(X[c], **kw)
        return set(p.index[m])

    rows = [
        score("IQR global", any_flag(outliers.iqr_flags, k=oc["iqr_k"]), err, legit, pop, val, distortion=dist, familia="estatística univariada",
              nota="total, frete e desconto, cada um contra a base inteira"),
        score("z-score (média/desvio)", any_flag(outliers.zscore_flags, thr=oc["zscore"]), err, legit, pop, val,
              distortion=dist, familia="estatística univariada", nota="|z| > 3"),
        score("z robusto (MAD) global", any_flag(outliers.mad_flags, thr=oc["mad_z"]), err, legit, pop, val,
              distortion=dist, familia="estatística univariada", nota="|z| > 3,5 sobre mediana e MAD"),
    ]
    grp = pd.Series(False, index=p.index)
    grp |= outliers.grouped(outliers.mad_flags, np.log1p(X["total_centavos"].clip(lower=0)), p["tipo_cliente"], thr=oc["mad_z"])
    grp |= outliers.grouped(outliers.mad_flags, np.log1p(X["frete_centavos"].clip(lower=0)), p["servico"].fillna("?"), thr=oc["mad_z"])
    rows.append(score("z robusto (MAD) por grupo", set(p.index[grp]), err, legit, pop, val, distortion=dist, familia="estatística univariada",
                      nota="log(total) por tipo de cliente; log(frete) por serviço"))
    feats = pd.DataFrame({
        "log_total": np.log1p(p["total_centavos"].astype(float).clip(lower=0)),
        "log_frete": np.log1p(p["frete_centavos"].astype(float).clip(lower=0)),
        "log_peso": np.log1p(p["peso_kg"].astype(float)),
        "desc_ratio": (p["desconto_centavos"] / p["subtotal_centavos"].clip(lower=1)).astype(float).clip(-1, 200),
        "frete_por_kg": np.log1p((p["frete_centavos"].astype(float).clip(lower=0) / 100) / p["peso_kg"].astype(float).clip(lower=0.05)),
        "n_itens": p["n_itens"].astype(float),
    }, index=p.index)
    m_if, _ = outliers.isolation_forest_flags(feats, oc["isolation_contamination"])
    rows.append(score("Isolation Forest", set(p.index[m_if]), err, legit, pop, val, distortion=dist, familia="multivariada",
                      nota=f"contaminação {oc['isolation_contamination']:.0%}; total, frete, peso, desconto, itens"))
    rule = (p["desconto_centavos"] > p["subtotal_centavos"]) | (p["total_centavos"] < 0) | \
        (p["valor_cobrado_centavos"].notna() & p["frete_centavos"].ne(p["valor_cobrado_centavos"]))
    rows.append(score("Regras de negócio", set(p.index[rule.fillna(False)]), err, legit, pop, val, distortion=dist, familia="regras",
                      nota="desconto <= subtotal; total >= 0; frete do pedido = frete da transportadora"))
    fcfg = cfg["frete"]
    # mesma padronização de UF do ETL, para comparar só a técnica de detecção
    end = text.AddressStandardizer().run(stg["enderecos"].drop_duplicates("endereco_id"), TreatmentLog())
    uf = freight.order_uf(p, end)
    models = freight.fit_freight_model(p["servico"], p["peso_kg"], uf, p["valor_cobrado_centavos"])
    z, _ = freight.robust_z(models, p["servico"], p["peso_kg"], uf, p["frete_centavos"])
    fz = (z.abs() > fcfg["mad_z_max"]).fillna(False)
    rows.append(score("Modelo frete x peso x UF (Huber + MAD)", set(p.index[fz]), err, legit, pop, val, distortion=dist, familia="multivariada",
                      nota="resíduo robusto de log(frete) dado log(peso) e UF, por serviço"))
    rows.append(score("Regras + modelo de frete (usado no ETL)", set(p.index[fz | rule.fillna(False)]), err, legit,
                      pop, val, distortion=dist, familia="combinação", nota="o que o pipeline aplica"))
    return pd.DataFrame(rows)


def item_values(stg: dict[str, pd.DataFrame], truth: Truth, cfg: dict) -> pd.DataFrame:
    it = stg["itens_pedido"].drop_duplicates("item_pedido_id").copy()
    it = it[~it["pedido_id"].isin(truth.orders(NOT_SALES))].set_index("item_pedido_id", drop=False)
    pop = set(it.index.astype(str))
    err = truth.keys("itens_pedido", ITEM_VALUE_ERRORS)
    legit_orders = truth.orders(["legitimo_b2b", "legitimo_viral"])
    legit = set(it.index[it["pedido_id"].isin(legit_orders)].astype(str))
    val = pd.Series(it["total_item_centavos"].astype(float).to_numpy(), index=it.index.astype(str))
    oc = cfg["outliers"]
    pr = it["preco_unitario_centavos"].astype(float)
    q = it["quantidade"].astype(float)

    def S(mask):
        return set(it.index[mask.fillna(False)].astype(str))

    ratio = pr / it["preco_tabela_centavos"].astype(float)
    pc = cfg["precos"]
    rule = (pr <= 0) | (q <= 0) | ~ratio.between(pc["razao_tabela_min"], pc["razao_tabela_max"])
    feats = pd.DataFrame({"log_preco": np.sign(pr) * np.log1p(pr.abs()), "q": q, "ratio": ratio.clip(-5, 500),
                          "log_total": np.log1p(it["total_item_centavos"].astype(float).abs())})
    m_if, _ = outliers.isolation_forest_flags(feats, oc["isolation_contamination"])
    rows = [
        score("IQR global (preço e quantidade)", S(outliers.iqr_flags(pr) | outliers.iqr_flags(q)), err, legit, pop, val,
              "estatística univariada"),
        score("z-score (média/desvio)", S(outliers.zscore_flags(pr) | outliers.zscore_flags(q)), err, legit, pop, val,
              "estatística univariada"),
        score("z robusto (MAD) global", S(outliers.mad_flags(pr) | outliers.mad_flags(q)), err, legit, pop, val,
              "estatística univariada"),
        score("z robusto (MAD) por variante", S(outliers.grouped(outliers.mad_flags, pr, it["variante_id"], thr=oc["mad_z"])),
              err, legit, pop, val, "estatística univariada", "preço comparado com o mesmo SKU"),
        score("Isolation Forest", S(m_if), err, legit, pop, val, "multivariada"),
        score("Regras de negócio (usado no ETL)", S(rule), err, legit, pop, val, "regras",
              "preço > 0, quantidade > 0, preço entre 55% e 100% da tabela"),
    ]
    return pd.DataFrame(rows)


def daily_series(stg: dict[str, pd.DataFrame], truth: Truth, cfg: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Receita diária como chega (staging): pegar o dia do replay sem acusar Black Friday nem o viral."""
    p = stg["pedidos"]
    paid = p[p["status"].isin(PAID)]
    s = paid.groupby(pd.to_datetime(paid["data_local"]))["total_centavos"].sum().astype(float)
    s = s[(s.index >= "2023-10-01") & (s.index <= "2026-10-13")].asfreq("D").fillna(0)
    days = pd.Index(s.index.strftime("%Y-%m-%d"))
    err = truth.days("reenvio_de_lote")
    legit = truth.days(["legitimo_black_friday", "legitimo_viral"])
    pop = set(days)
    oc = cfg["outliers"]
    cal = stg["calendario_comercial"]
    ev = cal.set_index(pd.to_datetime(cal["data"]))
    explained = (ev["nome_promocao"].notna() | ev["evento_comercial"].notna()).reindex(s.index).fillna(False).to_numpy()
    z = outliers.stl_residual_z(s, oc["stl_periodo"])
    up = lambda m: set(days[np.asarray(m)])  # noqa: E731
    q1, q3 = s.quantile([0.25, 0.75])
    rows = [
        score("IQR global", up(s > q3 + oc["iqr_k"] * (q3 - q1)), err, legit, pop, None, "série diária"),
        score("z-score (média/desvio)", up((s - s.mean()) / s.std() > oc["zscore"]), err, legit, pop, None, "série diária"),
        score("z robusto (MAD) global", up(outliers.robust_z(s) > oc["mad_z"]), err, legit, pop, None, "série diária"),
        score("STL (resíduo)", up(z > oc["stl_z"]), err, legit, pop, None, "série diária",
              "tendência + sazonalidade semanal; outlier = resíduo"),
        score("STL + calendário comercial", up((z > oc["stl_z"]) & ~explained), err, legit, pop, None, "série diária",
              "resíduo alto e sem data comercial que o explique"),
    ]
    series = pd.DataFrame({"data": days, "receita": s.to_numpy() / 100, "z_stl": z.to_numpy(),
                           "evento": ev["nome_promocao"].fillna(ev["evento_comercial"]).reindex(s.index).to_numpy(),
                           "erro": days.isin(list(err)), "legitimo": days.isin(list(legit))})
    return pd.DataFrame(rows), series


def duplicates_eval(stg: dict[str, pd.DataFrame], truth: Truth, cfg: dict) -> pd.DataFrame:
    p = stg["pedidos"].reset_index(drop=True)
    p["rid"] = p.index.astype(str)
    # Hash exato: cada linha repetida além da primeira é um acerto se o pedido foi duplicado/reenviado.
    cols = [c for c in p.columns if not c.startswith("_") and c not in ("rid", "data_local")]
    h = pd.util.hash_pandas_object(p[cols].astype("string").fillna(""), index=False)
    dup_rows = p[h.duplicated(keep="first")]
    exact_truth = truth.orders(["duplicata_exata", "reenvio_de_lote"])
    copies_expected = len(truth.log[truth.log["tipo_defeito"].isin(["duplicata_exata", "reenvio_de_lote"])])
    tp = int(dup_rows["pedido_id"].isin(exact_truth).sum())
    rows = [{"familia": "duplicatas", "tecnica": "Hash exato do conteúdo", "sinalizados": len(dup_rows), "acertos": tp,
             "erros_existentes": copies_expected, "precisao": tp / max(len(dup_rows), 1), "recall": tp / max(copies_expected, 1),
             "legitimos_derrubados": int(dup_rows["pedido_id"].isin(truth.legit_orders).sum()),
             "legitimos_total": len(truth.legit_orders), "outros_sinalizados": len(dup_rows) - tp,
             "nota": "mesma linha 2x (paginação, replay de lote)"}]
    base = p.drop_duplicates("pedido_id")
    it = stg["itens_pedido"].drop_duplicates("item_pedido_id")
    near_truth = truth.keys("pedidos", "duplicata_aproximada")
    legit = {str(x) for x in truth.legit_orders}
    bots_tests = {str(x) for x in truth.orders(["pedido_bot", "pedido_teste"])}
    pop = set(base["pedido_id"].astype(str))
    for name, w, sim in (("Janela de 10 min, sem olhar itens", 10, None),
                         ("Janela de 10 min + itens iguais (usado no ETL)", cfg["duplicatas"]["janela_minutos"],
                          cfg["duplicatas"]["similaridade_itens_min"]),
                         ("Janela de 2 h + itens iguais", 120, cfg["duplicatas"]["similaridade_itens_min"])):
        pairs = duplicates.near_duplicate_pairs(base, it, w, sim, cfg["duplicatas"]["tolerancia_total"])
        flagged = set(pairs["posterior"].astype(str))
        r = score(name, flagged, near_truth, legit, pop, None, "duplicatas")
        r["outros_sinalizados"] = len(flagged - near_truth - legit)
        r["nota"] = f"{len(flagged & bots_tests)} dos sinalizados são bots/testes (erro de outro tipo)"
        rows.append(r)
    return pd.DataFrame(rows)


def not_sales(stg: dict[str, pd.DataFrame], truth: Truth, cfg: dict) -> pd.DataFrame:
    p = stg["pedidos"].drop_duplicates("pedido_id")
    cli = stg["clientes"].drop_duplicates("cliente_id").copy()
    cli["email"] = cli["email"].str.strip().str.lower()
    log = TreatmentLog()
    pop = set(p["pedido_id"].astype(int))
    legit = truth.legit_orders
    test = suspicious.test_orders(p, cli, cfg["pedidos_teste"], log)
    rows = [score("Regra de pedido de teste", test, truth.orders("pedido_teste"), legit, pop, None, "não-vendas",
                  "conta interna ou total <= R$ 1,00")]
    b = cfg["bots"]
    rest = p[~p["pedido_id"].isin(test)]
    by_ip = suspicious.velocity_bursts(rest, "ip_checkout", b["janela_minutos"], b["min_pedidos_por_ip"])
    by_cli = suspicious.velocity_bursts(rest, "cliente_id", b["janela_minutos"], b["min_pedidos_por_cliente"])
    bots = truth.orders("pedido_bot")
    rows.append(score("Velocidade por IP", by_ip, bots, legit, pop, None, "não-vendas", "8+ pedidos do mesmo IP em 15 min"))
    rows.append(score("Velocidade por cliente", by_cli, bots, legit, pop, None, "não-vendas", "6+ pedidos da mesma conta em 15 min"))
    rows.append(score("IP ou cliente (usado no ETL)", by_ip | by_cli, bots, legit, pop, None, "não-vendas"))
    return pd.DataFrame(rows)
