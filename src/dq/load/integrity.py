"""Integridade referencial e janela de atraso: o que entra e o que vai para quarentena.

Papel: separar, depois da limpeza, os registros que não podem entrar nas
métricas porque falta o pai (item sem pedido, pedido sem cliente) ou porque
chegaram tarde demais para o período ainda aberto.
Entrada: tabelas da camada clean + ids de quarentena vindos do pipeline +
``late_window_days``.
Saída: tabelas prontas para carga + DataFrame de quarentena
(``tabela, chave, motivo, pedido_ref, lote, valor_centavos``).
Dependências: pandas.

Quarentena, e não exclusão: um item sem pedido pode ser um pedido que ainda
não chegou (o cabeçalho atrasou um dia). Ele fica guardado e a cada execução
é testado de novo; quando o pai aparece, entra. O relatório de qualidade
mostra quantos reais estão parados na quarentena, para que ninguém confunda
"não entrou na métrica" com "não existiu".

Janela de reprocessamento: pedido que chega até ``late_window_days`` depois da
data da compra entra e reabre aquele dia nos marts. Depois disso, o mês já foi
fechado e reportado; o pedido vai para quarentena com motivo
``atraso_fora_da_janela`` e só entra por decisão explícita (reabrir o período).
"""

from __future__ import annotations

import pandas as pd

QUARANTINE_COLUMNS = ["tabela", "chave", "motivo", "pedido_ref", "lote", "valor_centavos"]


def _q(df: pd.DataFrame, tabela: str, key: str, motivo: str, valor: str | None) -> pd.DataFrame:
    return pd.DataFrame({
        "tabela": tabela, "chave": df[key].astype("int64").astype(str), "motivo": motivo,
        "pedido_ref": df["pedido_id"].astype("Int64").astype("string") if "pedido_id" in df else None,
        "lote": pd.to_datetime(df["_lote"]).dt.date if "_lote" in df else None,
        "valor_centavos": df[valor].astype("Int64") if valor else pd.array([pd.NA] * len(df), dtype="Int64"),
    })


def apply_integrity(t: dict[str, pd.DataFrame], extra: dict[str, set], window_days: int,
                    log=None) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    t = dict(t)
    parts = []
    ped = t["pedidos"]

    lag = (pd.to_datetime(ped["_lote"]) - pd.to_datetime(ped["data_local"])).dt.days
    reasons = {
        "atraso_fora_da_janela": lag > window_days,
        "pedido_sem_cliente": ~ped["cliente_id"].isin(t["clientes"]["cliente_id"]),
    }
    for motivo, ids in extra.items():
        reasons[motivo] = ped["pedido_id"].isin(ids)
    taken = pd.Series(False, index=ped.index)
    for motivo, mask in reasons.items():
        m = mask & ~taken
        parts.append(_q(ped[m], "pedidos", "pedido_id", motivo, "total_centavos"))
        taken |= m
    if log is not None:
        log.add_many("pedidos", ped.loc[taken, "pedido_id"], "*", "quarentena_pedido", "quarentena")
    q_orders = set(ped.loc[taken, "pedido_id"])
    t["pedidos"] = ped[~taken]
    ok_orders = set(t["pedidos"]["pedido_id"])

    for name, key, valor in (("itens_pedido", "item_pedido_id", "total_item_centavos"), ("fretes", "frete_id", None)):
        df = t[name]
        parent_q = df["pedido_id"].isin(q_orders)
        orphan = ~df["pedido_id"].isin(ok_orders) & ~parent_q
        parts.append(_q(df[parent_q], name, key, "pai_em_quarentena", valor))
        parts.append(_q(df[orphan], name, key, f"{'item' if name == 'itens_pedido' else 'frete'}_sem_pedido", valor))
        t[name] = df[~parent_q & ~orphan]

    end = t["enderecos"]
    orphan = ~end["cliente_id"].isin(t["clientes"]["cliente_id"])
    parts.append(_q(end[orphan], "enderecos", "endereco_id", "endereco_sem_cliente", None))
    t["enderecos"] = end[~orphan]

    q = pd.concat([p for p in parts if len(p)], ignore_index=True) if any(len(p) for p in parts) else \
        pd.DataFrame(columns=QUARANTINE_COLUMNS)
    return t, q[QUARANTINE_COLUMNS]
