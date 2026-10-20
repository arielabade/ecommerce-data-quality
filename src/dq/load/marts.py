"""Marts: as métricas de negócio, recalculadas só onde algo mudou.

Papel: transformar a camada clean em métricas por dia, mês, região e
categoria. As mesmas consultas rodam sobre ``staging`` (antes da limpeza) e
sobre o gold (verdade) na avaliação; é assim que a diferença entre os três vira
"impacto da limpeza".
Entrada: um esquema DuckDB com ``pedidos``, ``itens_pedido``, ``enderecos``,
``regioes``, ``variantes``, ``produtos``, ``calendario_comercial``.
Saída: ``<dst>.vendas_diarias``, ``metricas_mensais``, ``receita_categoria_mensal``,
``clientes_resumo``, ``alertas_diarios``.
Dependências: duckdb, pandas, ``dq.clean.outliers`` (STL).

Definições (iguais às do dbt da origem):
- pedido pago: status ``pago``, ``enviado``, ``entregue`` ou ``devolvido``;
- receita: soma de ``total`` dos pedidos pagos; ticket médio = receita / pedidos pagos;
- taxa de cancelamento: cancelados / pedidos;
- recompra (mensal): % dos pedidos pagos do mês feitos por quem já tinha
  comprado antes; recompra (geral): % dos clientes com 2+ pedidos pagos.

Janela de reprocessamento: ``vendas_diarias`` apaga e recalcula só as datas
afetadas; os mensais recalculam os meses afetados e o seguinte (recompra
depende da ordem das compras). ``clientes_resumo`` e ``alertas_diarios``
dependem do histórico inteiro e são refeitos por completo (são pequenos).
"""

from __future__ import annotations

import pandas as pd

from dq.clean.outliers import stl_residual_z

PAID = "('pago','enviado','entregue','devolvido')"


def _orders(src: str) -> str:
    return f"""
    SELECT p.pedido_id, p.cliente_id, p.status, p.total_centavos, p.data_local,
           p.status IN {PAID} AS pago,
           coalesce(r.macrorregiao, 'Sem região') AS regiao,
           coalesce(r.uf, 'Sem UF') AS uf
    FROM {src}.pedidos p
    LEFT JOIN (SELECT DISTINCT endereco_id, uf FROM {src}.enderecos) e ON e.endereco_id = p.endereco_entrega_id
    LEFT JOIN {src}.regioes r ON r.uf = e.uf
    """


def _date_filter(col: str, dates: list | None) -> str:
    if dates is None:
        return ""
    if not dates:
        return " AND FALSE"
    lst = ",".join(f"DATE '{d}'" for d in sorted(set(dates)))
    return f" AND {col} IN ({lst})"


def build(con, src: str, dst: str, dates: list | None = None) -> dict:
    """Constrói (``dates=None``) ou reprocessa (lista de datas) os marts de ``dst`` a partir de ``src``."""
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {dst}")
    full = dates is None or not _exists(con, dst, "vendas_diarias")
    months = None if full else sorted({pd.Timestamp(d).strftime("%Y-%m-01") for d in dates} |
                                      {(pd.Timestamp(d) + pd.offsets.MonthBegin(1)).strftime("%Y-%m-01") for d in dates})
    daily_sql = f"""
        SELECT data_local, uf, regiao, count(*) AS pedidos, sum(pago::INT) AS pedidos_pagos,
               sum((status = 'cancelado')::INT) AS cancelados,
               coalesce(sum(CASE WHEN pago THEN total_centavos END), 0) AS receita_centavos
        FROM ({_orders(src)}) o WHERE TRUE {{f}} GROUP BY ALL"""
    seq = f"""
        WITH o AS ({_orders(src)}),
        s AS (SELECT o.*, row_number() OVER (PARTITION BY cliente_id ORDER BY data_local, pedido_id) AS seq
              FROM o WHERE pago)
        """
    monthly_sql = f"""{seq},
        allp AS (SELECT date_trunc('month', data_local)::DATE AS mes, regiao, count(*) AS pedidos,
                        sum((status = 'cancelado')::INT) AS cancelados FROM o GROUP BY ALL),
        paid AS (SELECT date_trunc('month', data_local)::DATE AS mes, regiao, count(*) AS pedidos_pagos,
                        sum(total_centavos) AS receita_centavos, sum((seq > 1)::INT) AS pedidos_recorrentes
                 FROM s GROUP BY ALL)
        SELECT a.mes, a.regiao, a.pedidos, coalesce(p.pedidos_pagos, 0) AS pedidos_pagos, a.cancelados,
               coalesce(p.receita_centavos, 0) AS receita_centavos, coalesce(p.pedidos_recorrentes, 0) AS pedidos_recorrentes
        FROM allp a LEFT JOIN paid p USING (mes, regiao) WHERE TRUE {{f}}"""
    cat_sql = f"""
        SELECT date_trunc('month', p.data_local)::DATE AS mes, coalesce(pr.categoria, 'Sem categoria') AS categoria,
               sum(i.total_item_centavos) AS receita_itens_centavos, count(*) AS itens
        FROM {src}.itens_pedido i JOIN (SELECT DISTINCT pedido_id, data_local, status FROM {src}.pedidos) p USING (pedido_id)
        LEFT JOIN {src}.variantes v USING (variante_id) LEFT JOIN {src}.produtos pr ON pr.produto_id = v.produto_id
        WHERE p.status IN {PAID} {{f}} GROUP BY ALL"""
    for name, sql, col, keys in (("vendas_diarias", daily_sql, "data_local", dates),
                                 ("metricas_mensais", monthly_sql, "a.mes", months),
                                 ("receita_categoria_mensal", cat_sql, "date_trunc('month', p.data_local)::DATE", months)):
        if full:
            con.execute(f"CREATE OR REPLACE TABLE {dst}.{name} AS {sql.format(f='')}")
        else:
            delcol = "data_local" if name == "vendas_diarias" else "mes"
            con.execute(f"DELETE FROM {dst}.{name} WHERE TRUE {_date_filter(delcol, keys)}")
            con.execute(f"INSERT INTO {dst}.{name} BY NAME {sql.format(f=_date_filter(col, keys))}")
    con.execute(f"""CREATE OR REPLACE TABLE {dst}.clientes_resumo AS
        SELECT cliente_id, count(*) AS pedidos_pagos, sum(total_centavos) AS receita_centavos,
               min(data_local) AS primeira_compra, max(data_local) AS ultima_compra
        FROM ({_orders(src)}) o WHERE pago GROUP BY cliente_id""")
    build_alerts(con, src, dst)
    return {"modo": "completo" if full else "reprocessamento", "datas": None if full else len(set(dates)),
            "meses": None if full else len(months)}


def build_alerts(con, src: str, dst: str) -> None:
    """Monitor da série diária: resíduo STL + calendário comercial.

    Um pico explicado por uma data comercial (Black Friday, Dia das Mães,
    promoção da loja) é registrado como esperado; um pico sem explicação vira
    alerta para alguém investigar. Ninguém apaga nada por causa deste mart.
    """
    d = con.execute(f"""SELECT data_local, sum(receita_centavos) AS receita, sum(pedidos) AS pedidos
                        FROM {dst}.vendas_diarias GROUP BY 1 ORDER BY 1""").df()
    if len(d) < 30:
        con.execute(f"CREATE OR REPLACE TABLE {dst}.alertas_diarios (data_local DATE, receita BIGINT, z_stl DOUBLE, evento VARCHAR, alerta BOOLEAN)")
        return
    s = d.set_index(pd.to_datetime(d["data_local"]))["receita"].astype(float)
    z = stl_residual_z(s, 7)
    cal = con.execute(f"""SELECT data, coalesce(nome_promocao, evento_comercial, nome_feriado) AS evento
                          FROM {src}.calendario_comercial""").df()
    ev = cal.set_index(pd.to_datetime(cal["data"]))["evento"]
    out = pd.DataFrame({"data_local": s.index.date, "receita": s.to_numpy().astype("int64"),
                        "z_stl": z.round(3).to_numpy(), "evento": ev.reindex(s.index).to_numpy()})
    out["alerta"] = (out["z_stl"].abs() > 4.0) & out["evento"].isna()
    con.register("_a", out)
    con.execute(f"CREATE OR REPLACE TABLE {dst}.alertas_diarios AS SELECT * FROM _a")
    con.unregister("_a")


def _exists(con, schema: str, name: str) -> bool:
    return bool(con.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema=? AND table_name=?",
                            [schema, name]).fetchone()[0])


def summary(con, dst: str) -> pd.DataFrame:
    """Totais do período (um número por métrica), usados nos relatórios."""
    return con.execute(f"""
        WITH m AS (SELECT sum(pedidos) p, sum(pedidos_pagos) pp, sum(cancelados) c, sum(receita_centavos) r,
                          sum(pedidos_recorrentes) rec FROM {dst}.metricas_mensais),
             c AS (SELECT avg((pedidos_pagos >= 2)::INT) AS recompra, count(*) AS clientes FROM {dst}.clientes_resumo)
        SELECT m.p AS pedidos, m.pp AS pedidos_pagos, m.r / 100.0 AS receita, m.r / 100.0 / nullif(m.pp, 0) AS ticket_medio,
               m.c / nullif(m.p, 0) AS taxa_cancelamento, m.rec / nullif(m.pp, 0) AS pedidos_recorrentes_pct,
               c.recompra AS taxa_recompra, c.clientes AS clientes_compradores
        FROM m, c""").df()
