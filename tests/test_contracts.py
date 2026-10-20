"""Contratos: a camada clean recusa o que viola regra de negócio e aceita o que cumpre."""

from __future__ import annotations

import pandas as pd
import pytest

from dq.contracts import ContractError, validate


def _orders(**over):
    d = {"pedido_id": pd.array([1, 2], dtype="Int64"), "cliente_id": pd.array([1, 1], dtype="Int64"),
         "status": ["entregue", "cancelado"],
         "criado_em": pd.to_datetime(["2025-01-10 12:00", "2025-01-11 12:00"], utc=True),
         "pago_em": pd.to_datetime(["2025-01-10 12:05", None], utc=True),
         "subtotal_centavos": pd.array([1000, 2000], dtype="Int64"), "desconto_centavos": pd.array([0, 100], dtype="Int64"),
         "frete_centavos": pd.array([500, 0], dtype="Int64"), "total_centavos": pd.array([1500, 1900], dtype="Int64"),
         "data_local": pd.to_datetime(["2025-01-10", "2025-01-11"]).date, "_lote": pd.to_datetime(["2025-01-10", "2025-01-11"]).date}
    d.update(over)
    return pd.DataFrame(d)


def test_valid_clean_orders_pass():
    assert validate("clean", {"pedidos": _orders()}, raise_on_fail=True).ok


@pytest.mark.parametrize("over", [
    {"desconto_centavos": pd.array([5000, 100], dtype="Int64"), "total_centavos": pd.array([-3500, 1900], dtype="Int64")},
    {"total_centavos": pd.array([9999, 1900], dtype="Int64")},
    {"pedido_id": pd.array([1, 1], dtype="Int64")},
    {"status": ["entregue", "perdido"]},
    {"data_local": pd.to_datetime(["2052-01-10", "2025-01-11"]).date},
])
def test_invalid_clean_orders_fail(over):
    with pytest.raises(ContractError):
        validate("clean", {"pedidos": _orders(**over)}, raise_on_fail=True)


def test_raw_contract_only_checks_shape():
    raw = pd.DataFrame({"pedido_id": ["1"], "criado_em": ["lixo"], "_lote": ["2025-01-10"]})
    assert validate("raw", {"pedidos": raw}, raise_on_fail=False).ok is False  # faltam colunas
    full = pd.DataFrame({c: ["x"] for c in ["pedido_id", "numero_pedido", "cliente_id", "endereco_entrega_id", "cupom_id",
                                            "canal_venda", "status", "criado_em", "pago_em", "cancelado_em", "subtotal",
                                            "desconto", "frete", "total", "ip_checkout"]} | {"_lote": ["2025-01-10"]})
    assert validate("raw", {"pedidos": full}, raise_on_fail=True).ok  # conteúdo sujo é permitido no raw


def test_marts_contract_rejects_negative_revenue():
    m = pd.DataFrame({"pedidos": [3], "receita_centavos": [-1]})
    with pytest.raises(ContractError):
        validate("marts", {"vendas_diarias": m}, raise_on_fail=True)
