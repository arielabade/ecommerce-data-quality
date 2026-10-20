"""Ponta a ponta no perfil pequeno: extrair, corromper, ETL em incrementos, reexecutar, avaliar."""

from __future__ import annotations

import duckdb
import pandas as pd
import pytest

from dq import etl
from dq.evaluate.__main__ import main as evaluate

pytestmark = pytest.mark.e2e

TABLES = ["pedidos", "itens_pedido", "fretes", "clientes", "enderecos"]


def _snapshot(path) -> dict[str, pd.DataFrame]:
    with duckdb.connect(str(path), read_only=True) as con:
        out = {t: con.execute(f'SELECT * EXCLUDE (_run_id) FROM clean."{t}" ORDER BY 1').df() for t in TABLES}
        out["marts"] = con.execute("SELECT * FROM marts.metricas_mensais ORDER BY mes, regiao").df()
        out["quarentena"] = con.execute("SELECT * FROM quarantine.registros ORDER BY tabela, chave").df()
    return out


@pytest.fixture(scope="module")
def pipeline_run(corrupted):
    reports = etl.main([], settings=corrupted, verbose=False)
    return corrupted, reports


def test_etl_increments_contracts_and_idempotence(pipeline_run):
    s, reports = pipeline_run
    assert len(reports) == 3  # dois incrementos + a reexecução de conferência
    assert all(c["ok"] for r in reports for c in r["contratos"].values())
    assert reports[-1]["idempotente"]
    assert reports[0]["quarentena"]["em_quarentena"] > 0


def test_running_the_whole_etl_again_gives_the_same_warehouse(pipeline_run):
    s, _ = pipeline_run
    before = _snapshot(s.path("warehouse"))
    again = etl.main([], settings=s, verbose=False)
    after = _snapshot(s.path("warehouse"))
    assert again[-1]["idempotente"]
    for k in before:
        pd.testing.assert_frame_equal(before[k].reset_index(drop=True), after[k].reset_index(drop=True), check_dtype=False)


def test_evaluation_scores_the_cleaning(pipeline_run):
    s, _ = pipeline_run
    out = evaluate(s)
    det = {(f, r["tecnica"]): r for f, rows in out["deteccao"].items() for r in rows}
    assert det[("nao_vendas", "IP ou cliente (usado no ETL)")]["recall"] == 1.0
    assert det[("precos_item", "Regras de negócio (usado no ETL)")]["precisao"] == 1.0
    assert det[("duplicatas", "Hash exato do conteúdo")]["precisao"] == 1.0
    m = out["metricas"]
    err_before = abs(m["antes"]["receita"] / m["verdade"]["receita"] - 1)
    err_after = abs(m["depois"]["receita"] / m["verdade"]["receita"] - 1)
    assert err_after < err_before
    assert m["depois"]["categorias_distintas"] <= m["verdade"]["categorias_distintas"] + 2
    assert (s.path("reports") / "evaluation.json").exists()
