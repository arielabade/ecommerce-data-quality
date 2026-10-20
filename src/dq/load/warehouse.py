"""Warehouse DuckDB e carga incremental idempotente.

Papel: gravar cada tabela da camada clean por chave natural, mudando só o que
mudou, e registrar cada execução.
Entrada: DataFrames da camada clean, quarentena, staging.
Saída: arquivo DuckDB (``paths.warehouse``) com os esquemas ``staging``,
``clean``, ``quarantine``, ``marts`` e ``meta``.
Dependências: duckdb, pandas.

Idempotência por construção: cada linha leva o hash do seu conteúdo de
negócio. Na carga, a linha nova entra, a que mudou de hash é substituída, a
que tem o mesmo hash não é tocada, e a que sumiu da entrada (dentro do escopo
de lotes desta execução) sai. Rodar duas vezes com a mesma entrada dá zero
inserções, zero atualizações e zero remoções, e o relatório da execução diz isso.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

from dq.clean.staging import row_hash

KEYS = {"pedidos": "pedido_id", "itens_pedido": "item_pedido_id", "fretes": "frete_id", "clientes": "cliente_id",
        "enderecos": "endereco_id", "produtos": "produto_id", "variantes": "variante_id",
        "categorias": "categoria_id", "regioes": "uf", "calendario_comercial": "data"}


def connect(path: Path) -> duckdb.DuckDBPyConnection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path), config={"TimeZone": "UTC"})  # não depender do fuso da máquina
    for schema in ("staging", "clean", "quarantine", "marts", "meta"):
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    con.execute("""CREATE TABLE IF NOT EXISTS meta.execucoes (
        run_id VARCHAR, corte VARCHAR, iniciado_em TIMESTAMPTZ, lotes_ate DATE, inseridos BIGINT,
        atualizados BIGINT, removidos BIGINT, inalterados BIGINT, quarentena BIGINT, idempotente BOOLEAN)""")
    con.execute("""CREATE TABLE IF NOT EXISTS quarantine.historico (
        tabela VARCHAR, chave VARCHAR, motivo VARCHAR, entrou_run VARCHAR, saiu_run VARCHAR)""")
    return con


def table_exists(con, schema: str, name: str) -> bool:
    return bool(con.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema=? AND table_name=?",
                            [schema, name]).fetchone()[0])


def _prepare(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for c in df.columns:
        if str(df[c].dtype) in ("str", "object") and c != "_lote":
            if df[c].map(lambda v: isinstance(v, (set, frozenset, list, dict))).any():
                df[c] = df[c].astype(str)
    return df


def replace(con, schema: str, name: str, df: pd.DataFrame) -> None:
    con.register("_df", _prepare(df))
    con.execute(f'CREATE OR REPLACE TABLE {schema}."{name}" AS SELECT * FROM _df')
    con.unregister("_df")


def upsert(con, name: str, df: pd.DataFrame, run_id: str, lote_max) -> dict:
    """Carrega ``df`` em ``clean.<name>`` por chave + hash. Devolve contagens e chaves tocadas."""
    key = KEYS[name]
    df = _prepare(df).reset_index(drop=True)
    cols = [c for c in df.columns if not c.startswith("_")]
    df["_hash"] = row_hash(df, cols).astype("uint64")
    df["_run_id"] = run_id
    if "_lote" not in df:
        df["_lote"] = pd.NaT
    if not table_exists(con, "clean", name):
        con.register("_in", df)
        con.execute(f'CREATE TABLE clean."{name}" AS SELECT * FROM _in')
        con.unregister("_in")
        return {"inseridos": len(df), "atualizados": 0, "removidos": 0, "inalterados": 0,
                "chaves": set(df[key].astype(str)), "removidas": set()}
    cur = con.execute(f'SELECT CAST("{key}" AS VARCHAR) AS k, _hash, _lote FROM clean."{name}"').df()
    inc = pd.DataFrame({"k": df[key].astype(str), "_hash": df["_hash"]})
    m = inc.merge(cur[["k", "_hash"]], on="k", how="left", suffixes=("", "_old"))
    new = m["_hash_old"].isna()
    changed = ~new & (m["_hash"] != m["_hash_old"])
    in_scope = cur["_lote"].isna() | (pd.to_datetime(cur["_lote"]) <= pd.Timestamp(lote_max)) if lote_max is not None \
        else pd.Series(True, index=cur.index)
    gone = cur.loc[in_scope & ~cur["k"].isin(inc["k"]), "k"]
    touched = pd.concat([m.loc[changed, "k"], gone])
    if len(touched):
        con.register("_del", pd.DataFrame({"k": touched}))
        con.execute(f'DELETE FROM clean."{name}" WHERE CAST("{key}" AS VARCHAR) IN (SELECT k FROM _del)')
        con.unregister("_del")
    write = df[(new | changed).to_numpy()].reset_index(drop=True)
    if len(write):
        con.register("_in", write)
        con.execute(f'INSERT INTO clean."{name}" BY NAME SELECT * FROM _in')
        con.unregister("_in")
    return {"inseridos": int(new.sum()), "atualizados": int(changed.sum()), "removidos": int(len(gone)),
            "inalterados": int((~new & ~changed).sum()),
            "chaves": set(m.loc[new | changed, "k"]), "removidas": set(gone)}


def read(con, schema: str, name: str) -> pd.DataFrame:
    return con.execute(f'SELECT * FROM {schema}."{name}"').df()


def update_quarantine(con, q: pd.DataFrame, run_id: str) -> dict:
    """Quarentena atual = a desta execução. Histórico guarda quando cada registro entrou e saiu."""
    prev = read(con, "quarantine", "registros") if table_exists(con, "quarantine", "registros") else \
        pd.DataFrame(columns=q.columns)
    replace(con, "quarantine", "registros", q)
    pk = ["tabela", "chave"]
    old = set(map(tuple, prev[pk].astype(str).to_numpy())) if len(prev) else set()
    now = set(map(tuple, q[pk].astype(str).to_numpy())) if len(q) else set()
    entered = q[[tuple(x) in (now - old) for x in q[pk].astype(str).to_numpy()]] if len(q) else q
    if len(entered):
        h = entered[["tabela", "chave", "motivo"]].assign(entrou_run=run_id, saiu_run=None)
        con.register("_h", h)
        con.execute("INSERT INTO quarantine.historico BY NAME SELECT * FROM _h")
        con.unregister("_h")
    resolved = old - now
    if resolved:
        con.register("_r", pd.DataFrame(list(resolved), columns=pk))
        con.execute("""UPDATE quarantine.historico h SET saiu_run = ? FROM _r
                       WHERE h.tabela = _r.tabela AND h.chave = _r.chave AND h.saiu_run IS NULL""", [run_id])
        con.unregister("_r")
    return {"em_quarentena": len(now), "entraram": len(now - old), "liberados": len(resolved)}
