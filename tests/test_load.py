"""Carga incremental: idempotente, detecta mudança, respeita o escopo de lotes."""

from __future__ import annotations

import pandas as pd

from dq.load import warehouse


def _df(**over):
    d = {"pedido_id": [1, 2, 3], "total_centavos": [100, 200, 300], "_lote": pd.to_datetime(["2025-01-01"] * 3).date}
    d.update(over)
    return pd.DataFrame(d)


def test_upsert_twice_changes_nothing(tmp_path):
    con = warehouse.connect(tmp_path / "w.duckdb")
    first = warehouse.upsert(con, "pedidos", _df(), "r1", "2025-01-01")
    second = warehouse.upsert(con, "pedidos", _df(), "r2", "2025-01-01")
    assert first["inseridos"] == 3
    assert (second["inseridos"], second["atualizados"], second["removidos"], second["inalterados"]) == (0, 0, 0, 3)
    assert set(con.execute("SELECT _run_id FROM clean.pedidos").df()["_run_id"]) == {"r1"}  # nada reescrito


def test_upsert_detects_insert_update_and_restatement(tmp_path):
    con = warehouse.connect(tmp_path / "w.duckdb")
    warehouse.upsert(con, "pedidos", _df(), "r1", "2025-01-01")
    new = pd.DataFrame({"pedido_id": [1, 2, 4], "total_centavos": [100, 999, 400],
                        "_lote": pd.to_datetime(["2025-01-01", "2025-01-01", "2025-01-02"]).date})
    r = warehouse.upsert(con, "pedidos", new, "r2", "2025-01-02")
    assert (r["inseridos"], r["atualizados"], r["removidos"], r["inalterados"]) == (1, 1, 1, 1)
    got = con.execute("SELECT pedido_id, total_centavos FROM clean.pedidos ORDER BY 1").df()
    assert got.to_dict("list") == {"pedido_id": [1, 2, 4], "total_centavos": [100, 999, 400]}


def test_rows_outside_batch_scope_are_not_deleted(tmp_path):
    con = warehouse.connect(tmp_path / "w.duckdb")
    df = _df(_lote=pd.to_datetime(["2025-01-01", "2025-01-01", "2025-01-05"]).date)
    warehouse.upsert(con, "pedidos", df, "r1", "2025-01-05")
    # uma execução que só recebeu lotes até 02/01 não pode apagar o pedido do lote de 05/01
    r = warehouse.upsert(con, "pedidos", df.iloc[:2], "r2", "2025-01-02")
    assert r["removidos"] == 0
    assert con.execute("SELECT count(*) FROM clean.pedidos").fetchone()[0] == 3


def test_quarantine_history_tracks_release(tmp_path):
    con = warehouse.connect(tmp_path / "w.duckdb")
    q1 = pd.DataFrame({"tabela": ["itens_pedido"], "chave": ["7"], "motivo": ["item_sem_pedido"], "pedido_ref": ["9"],
                       "lote": [None], "valor_centavos": [100]})
    a = warehouse.update_quarantine(con, q1, "r1")
    b = warehouse.update_quarantine(con, q1.iloc[:0], "r2")  # o pedido chegou: item liberado
    assert (a["entraram"], b["liberados"], b["em_quarentena"]) == (1, 1, 0)
    h = con.execute("SELECT entrou_run, saiu_run FROM quarantine.historico").fetchall()
    assert h == [("r1", "r2")]
