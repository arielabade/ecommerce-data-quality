"""O valor corrigido bate com a verdade?

Papel: para cada tipo de defeito que a limpeza tenta *corrigir* (e não só
detectar), medir quantos registros voltaram exatamente ao valor do gold, quantos
ficaram com outro valor e quantos ficaram de fora (removidos ou em quarentena).
Também mede o efeito colateral: valores que não tinham defeito e a limpeza
mudou para algo diferente do gold.
Entrada: camada clean (warehouse), quarentena, ``Truth``.
Saída: DataFrame por tipo de defeito.
Dependências: pandas.
"""

from __future__ import annotations

import pandas as pd

from dq.evaluate.truth import Truth

# tipo de defeito -> (tabela, chave, coluna no clean/gold, tolerância)
TARGETS = {
    "cidade_grafia": ("enderecos", "endereco_id", "cidade", None),
    "uf_grafia": ("enderecos", "endereco_id", "uf", None),
    "cep_formato": ("enderecos", "endereco_id", "cep", None),
    "categoria_grafia": ("produtos", "produto_id", "categoria", None),
    "email_sujo": ("clientes", "cliente_id", "email", None),
    "frete_absurdo": ("pedidos", "pedido_id", "frete_centavos", 0),
    "frete_peso_incoerente": ("pedidos", "pedido_id", "frete_centavos", 0),
    "desconto_maior_que_subtotal": ("pedidos", "pedido_id", "desconto_centavos", 0),
    "data_futura": ("pedidos", "pedido_id", "criado_em", pd.Timedelta(0)),
    "fuso_utc_sem_marcacao": ("pedidos", "pedido_id", "criado_em", pd.Timedelta(0)),
    "entrega_antes_da_compra": ("fretes", "frete_id", "entregue_em", pd.Timedelta(0)),
    "preco_zero": ("itens_pedido", "item_pedido_id", "preco_unitario_centavos", 0),
    "preco_negativo": ("itens_pedido", "item_pedido_id", "preco_unitario_centavos", 0),
    "preco_separador_perdido": ("itens_pedido", "item_pedido_id", "preco_unitario_centavos", 0),
    "quantidade_negativa": ("itens_pedido", "item_pedido_id", "quantidade", 0.001),
    "formato_moeda_br": ("pedidos", "pedido_id", "total_centavos", 0),
    "formato_data_iso_utc": ("pedidos", "pedido_id", "criado_em", pd.Timedelta(0)),
    "formato_data_br": ("pedidos", "pedido_id", "criado_em", pd.Timedelta(0)),
}


def _eq(a: pd.Series, b: pd.Series, tol) -> pd.Series:
    if tol is None:
        return a.astype("string").fillna("∅") == b.astype("string").fillna("∅")
    d = (a - b).abs()
    return (d <= tol).fillna(a.isna() & b.isna())


def _by_key(df: pd.DataFrame, key: str, col: str) -> pd.Series:
    """Coluna indexada pela chave em texto (uma linha por chave)."""
    out = df.assign(_k=df[key].astype(str)).drop_duplicates("_k").set_index("_k")[col]
    return out


def _num(v: pd.Series) -> pd.Series:
    if pd.api.types.is_datetime64_any_dtype(v):
        return v.astype("int64") / 3.6e12  # horas
    return pd.to_numeric(v, errors="coerce").astype(float)


def correction_accuracy(clean: dict[str, pd.DataFrame], truth: Truth,
                        staging: dict[str, pd.DataFrame] | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Restauração exata por tipo de defeito + erro médio antes (staging) e depois (clean)."""
    rows = []
    for tipo, (tab, key, col, tol) in TARGETS.items():
        keys = truth.keys(tab, tipo)
        if tipo.startswith("formato_"):
            # formato isolado: tira as linhas que também têm defeito de valor/data (medidos à parte)
            other = truth.log[(truth.log["tabela"] == tab) & ~truth.log["tipo_defeito"].str.startswith(("formato_", "legitimo"))
                              & (truth.log["tipo_defeito"] != "chegada_atrasada")]
            keys = keys - set(other["chave"])
        if not keys:
            continue
        g = _by_key(truth.gold[tab], key, col)
        c = _by_key(clean[tab], key, col)
        k = sorted(keys)
        present = [x for x in k if x in c.index]
        gv = g.reindex(present)
        cv = c.reindex(present)
        ok = _eq(cv, gv, tol)
        row = {"tipo_defeito": tipo, "tabela": tab, "coluna": col, "registros": len(k),
               "restaurados_exatos": int(ok.sum()), "valor_diferente": int((~ok).sum()),
               "fora_do_clean": len(k) - len(present),
               "taxa_restauracao": ok.sum() / len(k) if k else float("nan")}
        if tol is not None and staging is not None and tab in staging:
            sv = _by_key(staging[tab], key, col)
            scale = 100 if col.endswith("_centavos") else 1  # centavos -> reais
            row["erro_medio_antes"] = float((_num(sv.reindex(present)) - _num(gv)).abs().mean() / scale)
            row["erro_medio_depois"] = float((_num(cv) - _num(gv)).abs().mean() / scale)
        rows.append(row)
    out = pd.DataFrame(rows)
    side = []
    for tab, key, col in (("enderecos", "endereco_id", "cidade"), ("enderecos", "endereco_id", "uf"),
                          ("produtos", "produto_id", "categoria"), ("clientes", "cliente_id", "email")):
        touched = set(truth.log.loc[truth.log["tabela"] == tab, "chave"])
        g = _by_key(truth.gold[tab], key, col)
        c = _by_key(clean[tab], key, col)
        idx = [x for x in c.index if x in g.index and x not in touched]
        bad = ~_eq(c.reindex(idx), g.reindex(idx), None)
        side.append({"tabela": tab, "coluna": col, "valores_sem_defeito": len(idx), "alterados_indevidamente": int(bad.sum())})
    return out, pd.DataFrame(side)


def customer_unification(clean: dict[str, pd.DataFrame], truth: Truth) -> dict:
    moved = truth.log[(truth.log["tipo_defeito"] == "conta_duplicada") & (truth.log["tabela"] == "pedidos")]
    c = _by_key(clean["pedidos"], "pedido_id", "cliente_id")
    present = moved[moved["chave"].isin(c.index)]
    ok = (c.reindex(present["chave"]).astype(str).to_numpy() == present["valor_original"].to_numpy())
    accounts = truth.keys("clientes", "conta_duplicada")
    remaining = set(clean["clientes"]["cliente_id"].astype(str)) & accounts
    return {"contas_duplicadas": len(accounts), "contas_restantes_no_clean": len(remaining),
            "pedidos_reatribuidos": int(len(moved)), "pedidos_de_volta_ao_dono": int(ok.sum()),
            "taxa": float(ok.mean()) if len(ok) else float("nan")}


def quarantine_eval(q: pd.DataFrame, truth: Truth, window: int) -> pd.DataFrame:
    rows = []
    qk = {(r.tabela, str(r.chave)): r.motivo for r in q.itertuples()}
    for tipo, tab in (("item_orfao", "itens_pedido"), ("frete_orfao", "fretes"), ("pedido_orfao_cliente", "pedidos")):
        k = truth.keys(tab, tipo)
        hit = sum((tab, x) in qk for x in k)
        rows.append({"defeito": tipo, "registros": len(k), "em_quarentena": hit, "recall": hit / len(k) if k else float("nan")})
    late = truth.log[truth.log["tipo_defeito"] == "chegada_atrasada"].copy()
    late["dias"] = late["detalhe"].str.extract(r"(\d+)").astype(int)
    for label, m in (("atraso dentro da janela", late["dias"] <= window), ("atraso fora da janela", late["dias"] > window)):
        k = set(late.loc[m, "chave"])
        hit = sum(qk.get(("pedidos", x)) == "atraso_fora_da_janela" for x in k)
        rows.append({"defeito": label, "registros": len(k), "em_quarentena": hit,
                     "recall": hit / len(k) if k else float("nan")})
    fp = [k for k, mot in qk.items() if mot == "atraso_fora_da_janela" and k[1] not in set(late["chave"])]
    rows.append({"defeito": "quarentena por atraso sem atraso real", "registros": len(fp), "em_quarentena": len(fp),
                 "recall": float("nan")})
    return pd.DataFrame(rows)
