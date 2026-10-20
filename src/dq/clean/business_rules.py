"""Regras de negócio explícitas sobre valores: preço, quantidade, desconto e totais.

Papel: aplicar as identidades que a origem respeita em 100% das linhas (ver
``docs/DATA_SOURCE.md``) e usar a redundância entre colunas para *corrigir*,
não só detectar. Quando um valor tem uma segunda fonte (o subtotal do pedido
confere a soma dos itens; o preço de tabela limita o preço praticado), a
correção recupera o valor exato em vez de um palpite estatístico.
Entrada: pedidos e itens de staging.
Saída: tabelas corrigidas + ``TreatmentLog``.
Dependências: pandas, numpy.

Regras (``config/cleaning.yaml: precos``):
- ``quantidade > 0``; negativa com ``total_item`` positivo = sinal trocado -> abs.
- ``preco_unitario`` entre 55% e 100% do ``preco_tabela``:
  negativo com módulo plausível -> abs; razão ~100 -> vírgula perdida -> /100;
  zero -> reconstruído pelo subtotal do pedido menos os outros itens.
- ``total_item = quantidade x preco_unitario``.
- ``desconto <= subtotal``; desconto ~100x maior que o plausível = gravado em
  centavos -> /100; sem explicação -> limitado ao subtotal.
- ``total = subtotal - desconto + frete`` (recalculado depois do frete).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from dq.clean.audit import TreatmentLog


def fix_items(it: pd.DataFrame, ped: pd.DataFrame, cfg: dict, log: TreatmentLog) -> pd.DataFrame:
    it = it.copy()
    k = it["item_pedido_id"]
    lo, hi = cfg["razao_tabela_min"], cfg["razao_tabela_max"]
    s_lo, s_hi = cfg["separador_perdido"]

    neg_q = it["quantidade"] < 0
    log.add_many("itens_pedido", k[neg_q], "quantidade", "quantidade_negativa_sinal", "corrigido",
                 it.loc[neg_q, "quantidade"], -it.loc[neg_q, "quantidade"])
    it.loc[neg_q, "quantidade"] = -it.loc[neg_q, "quantidade"]

    p, tab = it["preco_unitario_centavos"], it["preco_tabela_centavos"]
    neg_p = (p < 0) & (-p / tab).between(lo, hi)
    log.add_many("itens_pedido", k[neg_p], "preco_unitario", "preco_negativo_sinal", "corrigido", p[neg_p], -p[neg_p])
    it.loc[neg_p, "preco_unitario_centavos"] = -p[neg_p]
    it.loc[neg_p & (it["total_item_centavos"] < 0), "total_item_centavos"] *= -1

    p = it["preco_unitario_centavos"]
    ratio = p / tab
    sep = ratio.between(s_lo, s_hi)
    new = (p[sep] / 100).round().astype("Int64")
    log.add_many("itens_pedido", k[sep], "preco_unitario", "preco_separador_decimal", "corrigido", p[sep], new)
    it.loc[sep, "preco_unitario_centavos"] = new
    tot_sep = sep & ((it["total_item_centavos"] / (it["quantidade"] * tab)).between(s_lo, s_hi))
    it.loc[tot_sep, "total_item_centavos"] = (it.loc[tot_sep, "total_item_centavos"] / 100).round().astype("Int64")

    # Preço zero: o subtotal do cabeçalho menos os outros itens dá o total deste item.
    zero = it["preco_unitario_centavos"] == 0
    if zero.any():
        subtotal = it["pedido_id"].map(ped.drop_duplicates("pedido_id").set_index("pedido_id")["subtotal_centavos"])
        others = it.groupby("pedido_id")["total_item_centavos"].transform("sum") - it["total_item_centavos"]
        n_zero = zero.groupby(it["pedido_id"]).transform("sum")
        rebuilt_total = (subtotal - others).where(zero & (n_zero == 1))
        rebuilt = (rebuilt_total / it["quantidade"]).round()
        ok = zero & (rebuilt / tab).between(lo, hi)
        log.add_many("itens_pedido", k[ok], "preco_unitario", "preco_zero_reconstruido_subtotal", "corrigido",
                     it.loc[ok, "preco_unitario_centavos"], rebuilt[ok].astype("Int64"))
        it.loc[ok, "preco_unitario_centavos"] = rebuilt[ok].astype("Int64")
        it.loc[ok, "total_item_centavos"] = rebuilt_total[ok].astype("Int64")
        rest = zero & ~ok
        log.add_many("itens_pedido", k[rest], "preco_unitario", "preco_zero_preco_tabela", "imputado",
                     it.loc[rest, "preco_unitario_centavos"], tab[rest])
        it.loc[rest, "preco_unitario_centavos"] = tab[rest]

    expected = (it["quantidade"] * it["preco_unitario_centavos"]).round().astype("Int64")
    mism = (expected - it["total_item_centavos"]).abs() > 1
    log.add_many("itens_pedido", k[mism], "total_item", "total_item_recalculado", "corrigido",
                 it.loc[mism, "total_item_centavos"], expected[mism])
    it.loc[mism, "total_item_centavos"] = expected[mism]

    ratio = it["preco_unitario_centavos"] / it["preco_tabela_centavos"]
    odd = ~ratio.between(lo, hi) & ratio.notna()
    log.add_many("itens_pedido", k[odd], "preco_unitario", "preco_fora_da_faixa_tabela", "sinalizado",
                 it.loc[odd, "preco_unitario_centavos"])
    return it


def fix_discount(ped: pd.DataFrame, log: TreatmentLog) -> pd.DataFrame:
    ped = ped.copy()
    bad = ped["desconto_centavos"] > ped["subtotal_centavos"]
    cents = bad & ((ped["desconto_centavos"] / 100) <= ped["subtotal_centavos"] * 0.5)
    k = ped["pedido_id"]
    new = (ped.loc[cents, "desconto_centavos"] / 100).round().astype("Int64")
    log.add_many("pedidos", k[cents], "desconto", "desconto_em_centavos", "corrigido", ped.loc[cents, "desconto_centavos"], new)
    ped.loc[cents, "desconto_centavos"] = new
    cap = bad & ~cents
    log.add_many("pedidos", k[cap], "desconto", "desconto_limitado_ao_subtotal", "limitado",
                 ped.loc[cap, "desconto_centavos"], ped.loc[cap, "subtotal_centavos"])
    ped.loc[cap, "desconto_centavos"] = ped.loc[cap, "subtotal_centavos"]
    return ped


def recompute_totals(ped: pd.DataFrame, log: TreatmentLog) -> pd.DataFrame:
    ped = ped.copy()
    expected = ped["subtotal_centavos"] - ped["desconto_centavos"] + ped["frete_centavos"]
    mism = expected.ne(ped["total_centavos"]).fillna(False) & expected.notna()
    log.add_many("pedidos", ped.loc[mism, "pedido_id"], "total", "total_recalculado", "corrigido",
                 ped.loc[mism, "total_centavos"], expected[mism])
    ped.loc[mism, "total_centavos"] = expected[mism]
    return ped


def check_subtotal_vs_items(ped: pd.DataFrame, it: pd.DataFrame, log: TreatmentLog) -> pd.Series:
    """Subtotal do cabeçalho x soma dos itens. Divergência restante = sinal, não correção."""
    s = it.groupby("pedido_id")["total_item_centavos"].sum()
    diff = ped["subtotal_centavos"] - ped["pedido_id"].map(s)
    bad = diff.abs().gt(np.maximum(2, 0.005 * ped["subtotal_centavos"].abs())).fillna(False)
    log.add_many("pedidos", ped.loc[bad, "pedido_id"], "subtotal", "subtotal_diverge_itens", "sinalizado",
                 ped.loc[bad, "subtotal_centavos"], ped.loc[bad, "pedido_id"].map(s))
    return bad
