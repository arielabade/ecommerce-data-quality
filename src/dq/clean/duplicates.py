"""Duplicatas: exatas (hash), aproximadas (janela de tempo + similaridade) e de cliente.

Papel: garantir que cada venda conte uma vez e cada pessoa seja um cliente.
Entrada: tabelas de staging (pedidos, itens, fretes, clientes).
Saída: tabelas deduplicadas + ``TreatmentLog``.
Dependências: pandas, numpy.

Três situações diferentes, três técnicas diferentes:
- **Exata** (mesma linha duas vezes: bug de paginação, replay de lote): hash do
  conteúdo de negócio, ignorando metadados de lote. Sem ambiguidade: remove.
- **Aproximada** (reenvio do checkout: novo id, mesmo cliente, mesmos itens,
  segundos depois): não há hash que pegue, porque id, número e horário mudam.
  Critério: mesmo cliente, até ``janela_minutos`` de distância, itens com
  Jaccard >= ``similaridade_itens_min`` e total dentro da tolerância. Só a
  janela não basta: um B2B que repõe estoque 1 h depois ou um bot que testa
  cartões fazem vários pedidos seguidos e não são reenvio.
- **Cliente duplicado** (mesma pessoa com ``+alias``): chave de identidade =
  e-mail canônico; os pedidos da conta nova passam para a conta mais antiga.
"""

from __future__ import annotations

import pandas as pd

from dq.clean.audit import TreatmentLog
from dq.clean.staging import row_hash

KEYS = {"pedidos": "pedido_id", "itens_pedido": "item_pedido_id", "fretes": "frete_id",
        "clientes": "cliente_id", "enderecos": "endereco_id"}
PAID = ("pago", "enviado", "entregue", "devolvido")


def drop_exact(df: pd.DataFrame, name: str, log: TreatmentLog) -> pd.DataFrame:
    """Remove cópias idênticas (conteúdo de negócio igual), mantendo a do lote mais antigo.

    Depois, se a mesma chave ainda aparece com conteúdo diferente, vale a
    versão do lote mais recente (é uma atualização, não uma duplicata).
    """
    key = KEYS[name]
    cols = [c for c in df.columns if not c.startswith("_")]
    df = df.assign(_h=row_hash(df, cols)).sort_values(["_lote", key], kind="stable")
    dup = df.duplicated("_h", keep="first")
    log.add_many(name, df.loc[dup, key], "*", "duplicata_exata_hash", "removido")
    df = df[~dup]
    upd = df.duplicated(key, keep="last")
    log.add_many(name, df.loc[upd, key], "*", "chave_repetida_versao_antiga", "removido")
    return df[~upd].drop(columns="_h").sort_values(key).reset_index(drop=True)


def unify_customers(cli: pd.DataFrame, ped: pd.DataFrame, log: TreatmentLog) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Contas com o mesmo e-mail canônico viram uma: fica a mais antiga (menor cadastro, menor id)."""
    c = cli.sort_values(["cadastrado_em", "cliente_id"])
    first = c.groupby("email")["cliente_id"].transform("first")
    merged = c[c["cliente_id"] != first]
    remap = dict(zip(merged["cliente_id"], first[merged.index], strict=True))
    log.add_many("clientes", merged["cliente_id"], "*", "conta_mesmo_email", "removido",
                 merged["cliente_id"], [remap[k] for k in merged["cliente_id"]])
    ped = ped.copy()
    moved = ped["cliente_id"].isin(remap)
    log.add_many("pedidos", ped.loc[moved, "pedido_id"], "cliente_id", "conta_mesmo_email", "corrigido",
                 ped.loc[moved, "cliente_id"], ped.loc[moved, "cliente_id"].map(remap))
    ped.loc[moved, "cliente_id"] = ped.loc[moved, "cliente_id"].map(remap).astype("Int64")
    return cli[~cli["cliente_id"].isin(remap)].reset_index(drop=True), ped


def item_signatures(itens: pd.DataFrame) -> pd.Series:
    """Conjunto ``variante:quantidade`` por pedido (base do Jaccard)."""
    sig = itens["variante_id"].astype("string") + ":" + itens["quantidade"].round(2).astype("string")
    return sig.groupby(itens["pedido_id"]).agg(lambda s: frozenset(s))


def near_duplicate_pairs(ped: pd.DataFrame, itens: pd.DataFrame, window_min: float, min_sim: float | None,
                         tol_total: float = 0.01) -> pd.DataFrame:
    """Pares (anterior, posterior) do mesmo cliente dentro da janela que passam no critério.

    ``min_sim=None`` usa só a janela (é a variante ingênua comparada na avaliação).
    """
    p = ped[["pedido_id", "cliente_id", "criado_em", "total_centavos", "status"]].dropna(
        subset=["cliente_id", "criado_em"]).sort_values(["cliente_id", "criado_em", "pedido_id"])
    sigs = item_signatures(itens) if min_sim is not None else None
    rows = []
    cid = p["cliente_id"].to_numpy()
    ts = p["criado_em"].astype("int64").to_numpy() // 1_000_000  # segundos
    ids = p["pedido_id"].to_numpy()
    tot = p["total_centavos"].astype("float64").to_numpy()
    w = window_min * 60
    for i in range(len(p)):
        j = i + 1
        while j < len(p) and cid[j] == cid[i] and ts[j] - ts[i] <= w:
            ok = True
            if min_sim is not None:
                a, b = sigs.get(ids[i], frozenset()), sigs.get(ids[j], frozenset())
                jac = len(a & b) / max(len(a | b), 1)
                same_total = abs(tot[i] - tot[j]) <= tol_total * max(abs(tot[i]), 1)
                ok = jac >= min_sim and same_total
            if ok:
                rows.append((ids[i], ids[j], (ts[j] - ts[i])))
            j += 1
    return pd.DataFrame(rows, columns=["anterior", "posterior", "segundos"])


def drop_near(ped: pd.DataFrame, itens: pd.DataFrame, fretes: pd.DataFrame, cfg: dict,
              log: TreatmentLog) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    pairs = near_duplicate_pairs(ped, itens, cfg["janela_minutos"], cfg["similaridade_itens_min"],
                                 cfg["tolerancia_total"])
    status = ped.set_index("pedido_id")["status"]
    keep_of: dict = {}
    for a, b in zip(pairs["anterior"], pairs["posterior"], strict=True):
        # Fica o que foi pago; empate = fica o primeiro (o reenvio é o segundo clique).
        loser, winner = (a, b) if (status.get(b) in PAID and status.get(a) not in PAID) else (b, a)
        keep_of.setdefault(loser, winner)
    drop = sorted(keep_of)
    log.add_many("pedidos", drop, "*", "duplicata_aproximada", "removido", None, [keep_of.get(d) for d in drop])
    m_i = itens["pedido_id"].isin(drop)
    log.add_many("itens_pedido", itens.loc[m_i, "item_pedido_id"], "*", "duplicata_aproximada", "removido")
    m_f = fretes["pedido_id"].isin(drop)
    log.add_many("fretes", fretes.loc[m_f, "frete_id"], "*", "duplicata_aproximada", "removido")
    return ped[~ped["pedido_id"].isin(drop)], itens[~m_i], fretes[~m_f]


def jaccard(a: frozenset, b: frozenset) -> float:
    return len(a & b) / max(len(a | b), 1) if (a or b) else 1.0

