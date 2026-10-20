"""Cada regra de limpeza: um caso em que ela age (positivo) e um em que não deve agir (negativo).

Os nomes de regra entre aspas neste arquivo são conferidos por
``test_catalog.py``: toda regra do código precisa aparecer aqui e em
``docs/CATALOG.md``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from dq.clean import business_rules, dates, duplicates, freight, nulls, outliers, suspicious, text
from dq.clean.audit import TreatmentLog
from dq.clean.pipeline import flag_outliers
from dq.clean.staging import parse_datetime, parse_money
from dq.load.integrity import apply_integrity
from dq.settings import get_settings

CFG = get_settings("small").cleaning
UTC = "UTC"


def rules(log: TreatmentLog) -> dict[str, set[str]]:
    f = log.frame()
    return f.groupby("regra")["chave"].apply(set).to_dict()


def ts(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz=UTC)


def orders(**cols) -> pd.DataFrame:
    n = len(next(iter(cols.values()))) if cols else 1
    base = {"pedido_id": pd.array(range(1, n + 1), dtype="Int64"), "cliente_id": pd.array([1] * n, dtype="Int64"),
            "status": ["entregue"] * n, "canal_venda": ["site"] * n, "endereco_entrega_id": pd.array([1] * n, dtype="Int64"),
            "criado_em": [ts("2025-01-10 15:00")] * n, "pago_em": [ts("2025-01-10 15:05")] * n,
            "subtotal_centavos": pd.array([10000] * n, dtype="Int64"), "desconto_centavos": pd.array([0] * n, dtype="Int64"),
            "frete_centavos": pd.array([2000] * n, dtype="Int64"), "total_centavos": pd.array([12000] * n, dtype="Int64"),
            "ip_checkout": [f"1.1.1.{i}" for i in range(n)], "_lote": [pd.Timestamp("2025-01-10").date()] * n}
    base.update(cols)
    df = pd.DataFrame(base)
    for c in ("criado_em", "pago_em"):
        df[c] = pd.to_datetime(df[c], utc=True)
    return df


# ---------------------------------------------------------------- staging
def test_parse_money_brazilian_and_us_formats():
    s = pd.Series(["1.234,56", "1234.56", "45,90", "-12.00", None])
    assert parse_money(s).tolist()[:4] == [123456, 123456, 4590, -1200]
    assert parse_money(s).isna().tolist()[4]


def test_parse_datetime_local_br_and_explicit_utc():
    s = pd.Series(["2024-05-03 13:22:01", "03/05/2024 13:22:01", "2024-05-03T16:22:01Z"])
    out = parse_datetime(s)
    assert (out == ts("2024-05-03 16:22:01")).all()  # os três são o mesmo instante


# ------------------------------------------------------------------- texto
def test_email_canonico_lowercases_trims_and_drops_alias():
    log = TreatmentLog()
    cli = pd.DataFrame({"cliente_id": [1, 2], "email": [" Maria+promo@X.com ", "joao@x.com"]})
    out = text.standardize_emails(cli, log)
    assert out["email"].tolist() == ["maria@x.com", "joao@x.com"]
    assert rules(log)["email_canonico"] == {"1"}  # o já canônico não é tocado


@pytest.fixture
def addr():
    return text.AddressStandardizer(CFG["texto"]["fuzzy_cidade_min"])


def _end(cidade, uf, cep="49000-000"):
    return pd.DataFrame({"endereco_id": pd.array([1], dtype="Int64"), "cidade": [cidade], "uf": [uf], "cep": [cep]})


def test_cidade_normalizacao_accent_case_and_uf_suffix(addr):
    for v in ("ARACAJU", "aracajú", "Aracaju - SE", "  Aracaju ", "aracaju/se"):
        log = TreatmentLog()
        out = addr.run(_end(v, "SE"), log)
        assert out["cidade"].iloc[0] == "Aracaju", v
        assert "cidade_normalizacao" in rules(log)


def test_cidade_sinonimo_and_aproximacao(addr):
    log = TreatmentLog()
    assert addr.run(_end("Sampa", "SP", "01310-100"), log)["cidade"].iloc[0] == "São Paulo"
    assert "cidade_sinonimo" in rules(log)
    log = TreatmentLog()
    assert addr.run(_end("Salavdor", "BA", "40000-000"), log)["cidade"].iloc[0] == "Salvador"
    assert "cidade_aproximacao" in rules(log)


def test_cidade_nao_reconhecida_is_flagged_not_guessed(addr):
    log = TreatmentLog()
    out = addr.run(_end("Xique-Xique", "BA", "40000-000"), log)
    assert out["cidade"].iloc[0] == "Xique-Xique"
    assert "cidade_nao_reconhecida" in rules(log)


def test_correct_city_is_untouched(addr):
    log = TreatmentLog()
    addr.run(_end("Aracaju", "SE"), log)
    assert not rules(log)


def test_uf_mapeamento_and_uf_desconhecida(addr):
    log = TreatmentLog()
    assert addr.run(_end("Aracaju", "Sergipe"), log)["uf"].iloc[0] == "SE"
    assert "uf_mapeamento" in rules(log)
    log = TreatmentLog()
    assert pd.isna(addr.run(_end("Aracaju", "XX"), log)["uf"].iloc[0])
    assert "uf_desconhecida" in rules(log)


def test_cep_formato_restores_lost_leading_zero(addr):
    log = TreatmentLog()
    out = addr.run(_end("São Paulo", "SP", "1310100"), log)
    assert out["cep"].iloc[0] == "01310-100"
    assert "cep_formato" in rules(log)


def test_cep_fora_da_faixa_da_uf_is_only_flagged(addr):
    log = TreatmentLog()
    out = addr.run(_end("Aracaju", "SE", "01310-100"), log)  # CEP de SP numa cidade de SE
    assert out["cep"].iloc[0] == "01310-100" and out["cep_inconsistente"].iloc[0]
    assert "cep_fora_da_faixa_da_uf" in rules(log)
    log = TreatmentLog()
    addr.run(_end("Aracaju", "SE", "49000-000"), log)
    assert "cep_fora_da_faixa_da_uf" not in rules(log)


@pytest.fixture
def categorias():
    return pd.DataFrame({"categoria_id": [1, 100, 110, 121], "categoria_pai_id": [None, 1, 1, 1],
                         "nome": ["Algodão", "Tricoline", "Malha PV", "Jeans"]})


def test_categoria_normalizacao_sinonimo_aproximacao_and_unmapped(categorias):
    prod = pd.DataFrame({"produto_id": [1, 2, 3, 4, 5],
                         "categoria": ["Tecido tricolines", "PV", "Tricolne", "Denim", "Tricoline"]})
    log = TreatmentLog()
    out = text.standardize_categories(prod, categorias, log, CFG["texto"]["fuzzy_categoria_min"] - 5)
    assert out["categoria"].tolist()[:3] == ["Tricoline", "Malha PV", "Tricoline"]
    r = rules(log)
    assert r["categoria_normalizacao"] == {"1"} and r["categoria_sinonimo"] == {"2"} and r["categoria_aproximacao"] == {"3"}
    assert r["categoria_nao_mapeada"] == {"4"} and pd.isna(out["categoria_id"].iloc[3])  # sinaliza, não chuta
    assert out["categoria_id"].iloc[4] == 100 and "5" not in set().union(*r.values())


# -------------------------------------------------------------- duplicatas
def test_duplicata_exata_hash_ignores_batch_metadata():
    df = pd.DataFrame({"pedido_id": [1, 1, 2], "v": [10, 10, 5], "_lote": ["2025-01-01", "2025-01-04", "2025-01-01"]})
    log = TreatmentLog()
    out = duplicates.drop_exact(df, "pedidos", log)
    assert out["pedido_id"].tolist() == [1, 2]
    assert rules(log)["duplicata_exata_hash"] == {"1"}


def test_chave_repetida_versao_antiga_keeps_latest_batch():
    df = pd.DataFrame({"pedido_id": [1, 1], "status": ["pago", "entregue"], "_lote": ["2025-01-01", "2025-01-03"]})
    log = TreatmentLog()
    out = duplicates.drop_exact(df, "pedidos", log)
    assert out["status"].tolist() == ["entregue"]
    assert "chave_repetida_versao_antiga" in rules(log)


def _items(rows):
    return pd.DataFrame(rows, columns=["item_pedido_id", "pedido_id", "variante_id", "quantidade"])


def test_duplicata_aproximada_same_items_seconds_apart():
    p = orders(criado_em=[ts("2025-01-10 15:00:00"), ts("2025-01-10 15:00:40")])
    it = _items([(1, 1, 7, 2.0), (2, 2, 7, 2.0)])
    fr = pd.DataFrame({"frete_id": [1, 2], "pedido_id": [1, 2]})
    log = TreatmentLog()
    p2, _, _ = duplicates.drop_near(p, it, fr, CFG["duplicatas"], log)
    assert p2["pedido_id"].tolist() == [1]
    assert rules(log)["duplicata_aproximada"] == {"2"}


def test_b2b_reorder_an_hour_later_is_not_a_duplicate():
    p = orders(criado_em=[ts("2025-01-10 15:00"), ts("2025-01-10 15:55")])
    it = _items([(1, 1, 7, 20.0), (2, 2, 9, 15.0)])
    fr = pd.DataFrame({"frete_id": [1, 2], "pedido_id": [1, 2]})
    log = TreatmentLog()
    p2, _, _ = duplicates.drop_near(p, it, fr, CFG["duplicatas"], log)
    assert len(p2) == 2 and not rules(log)


def test_conta_mesmo_email_moves_orders_to_oldest_account():
    cli = pd.DataFrame({"cliente_id": [10, 11, 12], "email": ["a@x.com", "a@x.com", "b@x.com"],
                        "cadastrado_em": [ts("2024-01-01"), ts("2025-01-01"), ts("2024-06-01")]})
    p = orders(cliente_id=pd.array([10, 11, 12], dtype="Int64"))
    log = TreatmentLog()
    c2, p2 = duplicates.unify_customers(cli, p, log)
    assert c2["cliente_id"].tolist() == [10, 12]
    assert p2["cliente_id"].tolist() == [10, 10, 12]
    assert rules(log)["conta_mesmo_email"] == {"11", "2"}


# ------------------------------------------------------- teste e bots
def test_pedido_teste_conta_interna_and_valor_simbolico():
    cli = pd.DataFrame({"cliente_id": [1, 2, 3], "email": ["qa@tecela.example", "ana@x.com", "bia@x.com"],
                        "nome": ["Teste QA", "Ana Souza", "Bia Lima"]})
    p = orders(cliente_id=pd.array([1, 2, 3], dtype="Int64"), total_centavos=pd.array([5000, 100, 445], dtype="Int64"))
    log = TreatmentLog()
    hit = suspicious.test_orders(p, cli, CFG["pedidos_teste"], log)
    assert hit == {1, 2}  # o pedido de R$ 4,45 é o menor pedido real possível
    r = rules(log)
    assert r["pedido_teste_conta_interna"] == {"1"} and r["pedido_teste_valor_simbolico"] == {"2"}


def test_bot_rajada_ip_and_cliente_vs_spread_orders():
    n = 10
    burst = orders(criado_em=[ts("2025-02-01 03:00") + pd.Timedelta(seconds=40 * i) for i in range(n)],
                   ip_checkout=["45.1.1.1"] * n, cliente_id=pd.array(range(100, 100 + n), dtype="Int64"))
    log = TreatmentLog()
    assert suspicious.bot_orders(burst, CFG["bots"], log) == set(range(1, n + 1))
    assert "bot_rajada_ip" in rules(log)
    same_cli = orders(criado_em=[ts("2025-02-01 03:00") + pd.Timedelta(seconds=60 * i) for i in range(7)],
                      ip_checkout=[f"9.9.9.{i}" for i in range(7)])
    log = TreatmentLog()
    assert len(suspicious.bot_orders(same_cli, CFG["bots"], log)) == 7
    assert "bot_rajada_cliente" in rules(log)
    spread = orders(criado_em=[ts("2025-02-01") + pd.Timedelta(days=i) for i in range(n)], ip_checkout=["45.1.1.1"] * n)
    log = TreatmentLog()
    assert suspicious.bot_orders(spread, CFG["bots"], log) == set()  # mesmo IP, mas um pedido por dia: cliente fiel


# ------------------------------------------------------------------ datas
def test_data_futura_ano_do_lote_and_irrecuperavel():
    p = orders(criado_em=[ts("2052-03-10 15:00"), ts("2025-03-10 15:00"), ts("2052-08-10 15:00")],
               _lote=[pd.Timestamp("2025-03-10").date()] * 3)
    log = TreatmentLog()
    out, lost = dates.fix_future_dates(p, log)
    assert out["criado_em"].iloc[0] == ts("2025-03-10 15:00")
    assert out["criado_em"].iloc[1] == ts("2025-03-10 15:00")  # data normal intocada
    assert lost == {3}  # agosto/2052 não cabe em nenhum ano plausível antes do lote de março
    r = rules(log)
    assert r["data_futura_ano_do_lote"] == {"1"} and r["data_futura_irrecuperavel"] == {"3"}


def test_fuso_utc_segmento_shifts_whole_segment_only():
    n = 10
    wpp = orders(canal_venda=["whatsapp"] * n,
                 criado_em=[ts("2025-03-05 18:00") + pd.Timedelta(days=i) for i in range(n)],
                 pago_em=[ts("2025-03-05 15:10") + pd.Timedelta(days=i) for i in range(n)])  # pago "3 h antes"
    site = orders(pedido_id=pd.array(range(100, 100 + n), dtype="Int64"),
                  criado_em=[ts("2025-03-05 18:00") + pd.Timedelta(days=i) for i in range(n)],
                  pago_em=[ts("2025-03-05 18:05") + pd.Timedelta(days=i) for i in range(n)])
    log = TreatmentLog()
    out, _ = dates.fix_timezone_segments(pd.concat([wpp, site], ignore_index=True), CFG["datas"], log)
    assert (out["criado_em"].iloc[:n] == wpp["criado_em"] - pd.Timedelta(hours=3)).all()
    assert (out["criado_em"].iloc[n:].to_numpy() == site["criado_em"].to_numpy()).all()
    assert len(rules(log)["fuso_utc_segmento"]) == n


def test_fuso_utc_individual_and_pagamento_antes_da_compra():
    p = orders(criado_em=[ts("2025-06-01 02:00"), ts("2025-06-10 12:00"), ts("2025-06-12 12:00")],
               pago_em=[ts("2025-06-01 00:10"), ts("2025-06-09 08:00"), ts("2025-06-12 12:05")])
    log = TreatmentLog()
    out, _ = dates.fix_timezone_segments(p, CFG["datas"], log)
    r = rules(log)
    assert r["fuso_utc_individual"] == {"1"}  # -1h50: assinatura do fuso
    assert r["pagamento_antes_da_compra"] == {"2"}  # -28 h: outra coisa, só sinaliza
    assert out["criado_em"].iloc[2] == ts("2025-06-12 12:00")


def test_entrega_antes_da_compra_fixed_or_nulled():
    p = orders(pedido_id=pd.array([1, 2, 3, 4], dtype="Int64"), criado_em=[ts("2025-03-01 12:00")] * 4)
    fr = pd.DataFrame({"frete_id": [1, 2, 3, 4], "pedido_id": [1, 2, 3, 4], "postado_em": [ts("2025-03-02 12:00")] * 4,
                       "entregue_em": [ts("2025-02-07 15:00"),   # 07/02 = 02/07 com dia e mês trocados
                                       ts("2024-03-09 15:00"),   # ano digitado errado
                                       ts("2025-01-20 15:00"),   # sem candidato plausível
                                       ts("2025-03-09 15:00")]})  # normal
    log = TreatmentLog()
    out = dates.fix_delivery_order(fr, p, CFG["datas"], log)
    assert out["entregue_em"].iloc[0] == ts("2025-07-02 15:00") or out["entregue_em"].iloc[0] == ts("2026-02-07 15:00") \
        or pd.isna(out["entregue_em"].iloc[0])
    assert out["entregue_em"].iloc[1] == ts("2025-03-09 15:00")
    assert pd.isna(out["entregue_em"].iloc[2])
    assert out["entregue_em"].iloc[3] == ts("2025-03-09 15:00")
    f = log.frame()
    assert set(f.loc[f["acao"] == "corrigido", "chave"]) >= {"2"} and set(f.loc[f["acao"] == "anulado", "chave"]) >= {"3"}
    assert "4" not in set(f["chave"]) and set(f["regra"]) == {"entrega_antes_da_compra"}


# ---------------------------------------------------------------- valores
def _it(**cols):
    base = {"item_pedido_id": pd.array([1, 2], dtype="Int64"), "pedido_id": pd.array([1, 1], dtype="Int64"),
            "quantidade": pd.array([2.0, 1.0], dtype="Float64"),
            "preco_unitario_centavos": pd.array([3000, 4000], dtype="Int64"),
            "preco_tabela_centavos": pd.array([3000, 4000], dtype="Int64"),
            "total_item_centavos": pd.array([6000, 4000], dtype="Int64")}
    base.update(cols)
    return pd.DataFrame(base)


def test_item_rules_positive_cases():
    ped = orders(subtotal_centavos=pd.array([10000], dtype="Int64"))
    pc = CFG["precos"]
    cases = {
        "quantidade_negativa_sinal": _it(quantidade=pd.array([-2.0, 1.0], dtype="Float64")),
        "preco_negativo_sinal": _it(preco_unitario_centavos=pd.array([-3000, 4000], dtype="Int64"),
                                    total_item_centavos=pd.array([-6000, 4000], dtype="Int64")),
        "preco_separador_decimal": _it(preco_unitario_centavos=pd.array([300000, 4000], dtype="Int64"),
                                       total_item_centavos=pd.array([600000, 4000], dtype="Int64")),
        "preco_zero_reconstruido_subtotal": _it(preco_unitario_centavos=pd.array([0, 4000], dtype="Int64"),
                                                total_item_centavos=pd.array([0, 4000], dtype="Int64")),
    }
    for rule, it in cases.items():
        log = TreatmentLog()
        out = business_rules.fix_items(it, ped, pc, log)
        assert out["preco_unitario_centavos"].tolist() == [3000, 4000], rule
        assert out["total_item_centavos"].tolist() == [6000, 4000], rule
        assert rule in rules(log), rule


def test_item_rules_fallbacks_and_flags():
    pc = CFG["precos"]
    ped = orders(subtotal_centavos=pd.array([99999], dtype="Int64"))  # subtotal não fecha: reconstrução impossível
    log = TreatmentLog()
    out = business_rules.fix_items(_it(preco_unitario_centavos=pd.array([0, 4000], dtype="Int64"),
                                       total_item_centavos=pd.array([0, 4000], dtype="Int64")), ped, pc, log)
    assert out["preco_unitario_centavos"].iloc[0] == 3000 and "preco_zero_preco_tabela" in rules(log)
    assert "total_item_recalculado" in rules(log)
    log = TreatmentLog()
    business_rules.fix_items(_it(preco_unitario_centavos=pd.array([1000, 4000], dtype="Int64"),
                                 total_item_centavos=pd.array([2000, 4000], dtype="Int64")), ped, pc, log)
    assert rules(log) == {"preco_fora_da_faixa_tabela": {"1"}}


def test_clean_items_are_untouched():
    log = TreatmentLog()
    business_rules.fix_items(_it(), orders(), CFG["precos"], log)
    assert not rules(log)


def test_desconto_em_centavos_and_limitado():
    p = orders(desconto_centavos=pd.array([150000, 2000000, 1500], dtype="Int64"),
               subtotal_centavos=pd.array([10000, 10000, 10000], dtype="Int64"))
    log = TreatmentLog()
    out = business_rules.fix_discount(p, log)
    assert out["desconto_centavos"].tolist() == [1500, 10000, 1500]
    r = rules(log)
    assert r["desconto_em_centavos"] == {"1"} and r["desconto_limitado_ao_subtotal"] == {"2"}


def test_total_recalculado_and_subtotal_diverge_itens():
    p = orders(total_centavos=pd.array([99999, 12000], dtype="Int64"))
    log = TreatmentLog()
    out = business_rules.recompute_totals(p, log)
    assert out["total_centavos"].tolist() == [12000, 12000] and rules(log)["total_recalculado"] == {"1"}
    it = pd.DataFrame({"pedido_id": [1, 2], "total_item_centavos": [10000, 5000]})
    log = TreatmentLog()
    business_rules.check_subtotal_vs_items(out, it, log)
    assert rules(log) == {"subtotal_diverge_itens": {"2"}}


# ------------------------------------------------------------------ frete
def _freight_world(n=400, seed=0):
    rng = np.random.default_rng(seed)
    peso = rng.uniform(0.3, 5, n)
    uf = rng.choice(["SP", "BA", "RS"], n)
    eff = pd.Series(uf).map({"SP": 0.0, "BA": 0.5, "RS": 0.3}).to_numpy()
    frete = np.round(np.exp(2.8 + 0.15 * np.log(peso) + eff + rng.normal(0, 0.03, n)) * 100).astype(int)
    p = orders(pedido_id=pd.array(range(1, n + 1), dtype="Int64"), endereco_entrega_id=pd.array(range(1, n + 1), dtype="Int64"),
               frete_centavos=pd.array(frete, dtype="Int64"))
    fr = pd.DataFrame({"frete_id": range(1, n + 1), "pedido_id": range(1, n + 1), "servico": "PAC", "peso_kg": peso,
                       "valor_cobrado_centavos": pd.array(frete, dtype="Int64")})
    end = pd.DataFrame({"endereco_id": range(1, n + 1), "uf": uf})
    return p, fr, end, frete


def test_frete_conciliado_transportadora_restores_exact_value():
    p, fr, end, frete = _freight_world()
    p.loc[0, "frete_centavos"] = int(frete[0]) * 100  # export do pedido em centavos; transportadora certa
    log = TreatmentLog()
    out, _ = freight.reconcile_freight(p, fr, end, CFG["frete"], log)
    assert out["frete_centavos"].iloc[0] == frete[0]
    assert rules(log)["frete_conciliado_transportadora"] == {"1"}


def test_frete_conciliado_pedido_when_carrier_value_is_wrong():
    p, fr, end, frete = _freight_world()
    fr.loc[0, "valor_cobrado_centavos"] = int(frete[0]) * 100
    log = TreatmentLog()
    _, fr2 = freight.reconcile_freight(p, fr, end, CFG["frete"], log)
    assert fr2["valor_cobrado_centavos"].iloc[0] == frete[0]
    assert rules(log)["frete_conciliado_pedido"] == {"1"}


def test_frete_implausivel_para_o_peso_imputed_and_normal_untouched():
    p, fr, end, frete = _freight_world()
    p.loc[0, "frete_centavos"] = fr.loc[0, "valor_cobrado_centavos"] = 999999  # os dois sistemas com o placeholder
    log = TreatmentLog()
    out, _ = freight.reconcile_freight(p, fr, end, CFG["frete"], log)
    assert abs(out["frete_centavos"].iloc[0] / frete[0] - 1) < 0.15
    assert "1" in rules(log)["frete_implausivel_para_o_peso"]
    assert len(rules(log)["frete_implausivel_para_o_peso"]) <= 4  # ruído normal de 3% quase nunca passa de z=4


# ------------------------------------------------------------------ nulos
def test_custo_real_imputation_and_mechanism_diagnosis():
    rng = np.random.default_rng(1)
    n = 600
    tr = rng.choice(["Correios", "Jadlog"], n)
    peso = rng.uniform(0.2, 6, n)
    cobrado = np.round(1500 + 400 * peso).astype(int)
    custo = pd.array(np.round(cobrado * 0.9).astype(int), dtype="Int64")
    fr = pd.DataFrame({"frete_id": range(n), "transportadora": tr, "servico": np.where(tr == "Jadlog", "Package", "PAC"),
                       "peso_kg": peso, "valor_cobrado_centavos": cobrado, "frete_gratis": 0, "custo_real_centavos": custo})
    miss = (tr == "Jadlog") & (rng.random(n) < 0.5)  # MAR: depende da transportadora
    fr.loc[miss, "custo_real_centavos"] = pd.NA
    diag = nulls.diagnose(fr)
    assert "não é MCAR" in diag["leitura"]
    log = TreatmentLog()
    out, _ = nulls.impute_freight_cost(fr, "modelo", log)
    err = (out.loc[miss, "custo_real_centavos"].astype(float) - np.round(cobrado[miss] * 0.9)).abs() / (cobrado[miss] * 0.9)
    assert err.mean() < 0.05 and out["custo_real_imputado"].sum() == miss.sum()
    assert "custo_real_modelo" in rules(log)
    log = TreatmentLog()
    nulls.impute_freight_cost(fr, "mediana_grupo", log)
    assert "custo_real_mediana_grupo" in rules(log)
    log = TreatmentLog()
    out, _ = nulls.impute_freight_cost(fr, "exclusao", log)
    assert out["custo_real_centavos"].isna().sum() == miss.sum() and not rules(log)


# --------------------------------------------------------------- outliers
def test_ticket_atipico_para_o_segmento_flags_only_within_group():
    rng = np.random.default_rng(0)
    totals = np.r_[rng.normal(15000, 3000, 200), rng.normal(400000, 50000, 30), [5_000_000]].round().astype(int)
    tipos = ["hobby"] * 200 + ["confeccao"] * 31
    p = orders(pedido_id=pd.array(range(1, 232), dtype="Int64"), cliente_id=pd.array(range(1, 232), dtype="Int64"),
               total_centavos=pd.array(totals, dtype="Int64"))
    cli = pd.DataFrame({"cliente_id": range(1, 232), "tipo_cliente": tipos})
    log = TreatmentLog()
    out = flag_outliers(p, cli, CFG["outliers"], log)
    flagged = rules(log).get("ticket_atipico_para_o_segmento", set())
    assert "231" in flagged  # R$ 50 mil mesmo para confecção
    assert not ({str(i) for i in range(201, 231)} & flagged)  # confecção comparada com confecção
    assert (out["total_centavos"] == p["total_centavos"]).all()  # só sinaliza


def test_detectors_iqr_mad_zscore_stl_isolation():
    x = pd.Series(np.r_[np.random.default_rng(0).normal(100, 10, 500), [400, 900]])
    assert outliers.iqr_flags(x).iloc[-2:].all() and outliers.mad_flags(x).iloc[-2:].all()
    contaminated = pd.Series(np.r_[np.full(85, 100.0), np.full(15, 10_000.0)])  # 15% de lixo
    assert outliers.mad_flags(contaminated).iloc[-5:].all()
    assert not outliers.zscore_flags(contaminated, 3.0).iloc[-5:].all()  # média/desvio contaminados
    idx = pd.date_range("2024-01-01", periods=140, freq="D")
    s = pd.Series(1000 + 200 * (idx.dayofweek >= 5), index=idx, dtype=float) + np.random.default_rng(1).normal(0, 10, 140)
    s.iloc[70] *= 3
    assert outliers.stl_flags(s).iloc[70] and outliers.stl_flags(s).sum() <= 3
    X = pd.DataFrame({"a": np.r_[np.random.default_rng(2).normal(0, 1, 300), [8]], "b": np.r_[np.random.default_rng(3).normal(0, 1, 300), [-8]]})
    m, _ = outliers.isolation_forest_flags(X, 0.01)
    assert m.iloc[-1]


# ------------------------------------------------------------- integridade
def test_quarantine_reasons_and_late_window():
    p = orders(pedido_id=pd.array([1, 2, 3], dtype="Int64"), cliente_id=pd.array([1, 1, 99], dtype="Int64"),
               _lote=[pd.Timestamp("2025-01-10").date(), pd.Timestamp("2025-01-25").date(), pd.Timestamp("2025-01-10").date()])
    p["data_local"] = pd.Timestamp("2025-01-10").date()
    t = {"pedidos": p, "clientes": pd.DataFrame({"cliente_id": [1]}),
         "enderecos": pd.DataFrame({"endereco_id": [1, 2], "cliente_id": [1, 99]}),
         "itens_pedido": pd.DataFrame({"item_pedido_id": [1, 2, 3], "pedido_id": [1, 2, 77], "total_item_centavos": [1, 2, 3],
                                       "_lote": ["2025-01-10"] * 3}),
         "fretes": pd.DataFrame({"frete_id": [1], "pedido_id": [77], "_lote": ["2025-01-10"]})}
    out, q = apply_integrity(t, {"data_irrecuperavel": set()}, 7)
    got = {(r.tabela, r.chave): r.motivo for r in q.itertuples()}
    assert got[("pedidos", "2")] == "atraso_fora_da_janela"
    assert got[("pedidos", "3")] == "pedido_sem_cliente"
    assert got[("itens_pedido", "2")] == "pai_em_quarentena"
    assert got[("itens_pedido", "3")] == "item_sem_pedido"
    assert got[("fretes", "1")] == "frete_sem_pedido"
    assert got[("enderecos", "2")] == "endereco_sem_cliente"
    assert out["pedidos"]["pedido_id"].tolist() == [1] and out["itens_pedido"]["item_pedido_id"].tolist() == [1]
    # "data_irrecuperavel" e "quarentena_pedido" vêm do mesmo caminho (motivo extra do pipeline)
    _, q2 = apply_integrity(t, {"data_irrecuperavel": {1}}, 7, TreatmentLog())
    assert dict(((r.tabela, r.chave), r.motivo) for r in q2.itertuples())[("pedidos", "1")] == "data_irrecuperavel"
