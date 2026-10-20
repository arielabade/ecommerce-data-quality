"""Injetor: reprodutível, registra tudo e não toca nos eventos legítimos."""

from __future__ import annotations

import hashlib

import pandas as pd

from conftest import make_settings
from dq.corrupt.__main__ import run as corrupt
from dq.extract.source import extract


def _digest(directory) -> dict[str, str]:
    out = {}
    for p in sorted(directory.glob("*.parquet")):
        df = pd.read_parquet(p)
        out[p.name] = hashlib.sha256(pd.util.hash_pandas_object(df.astype("string"), index=False).to_numpy().tobytes()).hexdigest()
    return out


def test_same_seed_same_raw_and_same_log(tmp_path):
    a, b = make_settings(tmp_path / "a"), make_settings(tmp_path / "b")
    for s in (a, b):
        extract(s)
        corrupt(s)
    assert _digest(a.path("raw")) == _digest(b.path("raw"))
    assert _digest(a.path("defect_log")) == _digest(b.path("defect_log"))
    assert _digest(a.path("gold")) == _digest(b.path("gold"))


def test_different_seed_changes_the_dirt(tmp_path):
    a, b = make_settings(tmp_path / "a"), make_settings(tmp_path / "b")
    b.corruption["seed"] = 1
    for s in (a, b):
        extract(s)
        corrupt(s)
    assert _digest(a.path("raw"))["pedidos.parquet"] != _digest(b.path("raw"))["pedidos.parquet"]
    assert _digest(a.path("gold")) == _digest(b.path("gold"))  # a verdade não depende do seed da sujeira


def test_log_has_every_defect_family_and_legit_events(corrupted):
    log = pd.read_parquet(corrupted.path("defect_log") / "defects.parquet")
    expected = {"duplicata_exata", "reenvio_de_lote", "duplicata_aproximada", "frete_absurdo", "desconto_maior_que_subtotal",
                "frete_peso_incoerente", "data_futura", "fuso_utc_sem_marcacao", "chegada_atrasada", "pedido_ausente",
                "item_orfao", "pedido_orfao_cliente", "pedido_teste", "pedido_bot", "preco_zero", "preco_negativo",
                "preco_separador_perdido", "quantidade_negativa", "entrega_antes_da_compra", "nulo_mcar", "nulo_mar",
                "nulo_mnar", "email_sujo", "conta_duplicada", "cidade_grafia", "uf_grafia", "cep_formato",
                "categoria_grafia", "formato_moeda_br", "formato_data_br", "formato_data_iso_utc",
                "legitimo_b2b", "legitimo_viral", "legitimo_black_friday"}
    assert expected <= set(log["tipo_defeito"])
    assert set(log.columns) >= {"tabela", "chave", "coluna", "tipo_defeito", "valor_original", "valor_sujo", "legitimo"}
    assert log.loc[log["tipo_defeito"].str.startswith("legitimo"), "legitimo"].all()


def test_legit_orders_never_receive_value_defects(corrupted):
    log = pd.read_parquet(corrupted.path("defect_log") / "defects.parquet")
    legit = set(log.loc[log["legitimo"], "pedido_ref"].dropna())
    value = log[log["tipo_defeito"].isin(["frete_absurdo", "desconto_maior_que_subtotal", "frete_peso_incoerente",
                                          "preco_zero", "preco_negativo", "preco_separador_perdido"])]
    assert not (set(value["pedido_ref"].dropna()) & legit)


def test_gold_respects_accounting_identities(corrupted):
    g = pd.read_parquet(corrupted.path("gold") / "pedidos.parquet")
    assert (g["total_centavos"] == g["subtotal_centavos"] - g["desconto_centavos"] + g["frete_centavos"]).all()
    assert (g["desconto_centavos"] <= g["subtotal_centavos"]).all()


def test_raw_is_all_text(corrupted):
    for p in corrupted.path("raw").glob("*.parquet"):
        df = pd.read_parquet(p)
        assert all(str(t) in ("string", "str", "object") for t in df.dtypes), p.name
