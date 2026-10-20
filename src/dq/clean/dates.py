"""Datas: futuro, fuso misturado e ordem impossível.

Papel: garantir que cada evento de um pedido aconteça numa data possível e na
ordem certa (compra <= pagamento <= postagem <= entrega <= hoje).
Entrada: pedidos e fretes de staging (datas já em UTC), com ``_lote``.
Saída: tabelas corrigidas + ``TreatmentLog``; pedidos irrecuperáveis vão para
quarentena pelo chamador.
Dependências: pandas, numpy.

Armazenamento em UTC, apresentação em ``America/Sao_Paulo``: toda comparação
aqui é entre instantes UTC; a data de negócio (o "dia" do pedido) é calculada
só na hora de agregar.
"""

from __future__ import annotations

import pandas as pd

from dq.clean.audit import TreatmentLog
from dq.clean.staging import TZ_LOCAL


def fix_future_dates(ped: pd.DataFrame, log: TreatmentLog) -> tuple[pd.DataFrame, set[int]]:
    """Data de compra depois do dia em que o registro chegou: ano digitado errado.

    O ``_lote`` (dia em que o registro entrou no ETL) é metadado confiável e
    limita a data real por cima. Tenta-se o mesmo dia/mês/hora com o ano do
    lote e o ano anterior; vale o candidato mais próximo do lote que não passe
    dele. Sem candidato plausível (até 60 dias antes do lote), o pedido vai
    para quarentena: inventar uma data mudaria a receita de um mês qualquer.
    """
    ped = ped.copy()
    # +2 dias: o lote fecha no fim do dia e um pedido gravado em UTC sem marcação
    # (até 3 h adiantado) pode cair no dia seguinte sem ser "futuro" de verdade.
    lote_end = pd.to_datetime(ped["_lote"]).dt.tz_localize(TZ_LOCAL) + pd.Timedelta(days=2)
    lote_end = lote_end.dt.tz_convert("UTC")
    fut = ped["criado_em"] > lote_end
    fixed, lost = [], set()
    for i in ped.index[fut]:
        ts, lim = ped.at[i, "criado_em"], lote_end[i]
        best = None
        for y in (lim.year, lim.year - 1):
            try:
                cand = ts.replace(year=y)
            except ValueError:
                continue
            if lim - pd.Timedelta(days=61) <= cand <= lim and (best is None or cand > best):
                best = cand
        if best is None:
            lost.add(int(ped.at[i, "pedido_id"]))
        else:
            fixed.append((i, ts, best))
    for i, _old, new in fixed:
        ped.at[i, "criado_em"] = new
    log.add_many("pedidos", [ped.at[i, "pedido_id"] for i, _, _ in fixed], "criado_em", "data_futura_ano_do_lote",
                 "corrigido", [o.isoformat() for _, o, _ in fixed], [n.isoformat() for _, _, n in fixed])
    log.add_many("pedidos", sorted(lost), "criado_em", "data_futura_irrecuperavel", "quarentena")
    return ped, lost


def fix_timezone_segments(ped: pd.DataFrame, cfg: dict, log: TreatmentLog) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Detecta segmentos (canal x mês) gravados em UTC sem marcação e desloca -3 h.

    Sinal: pagamento registrado *antes* da compra. Na origem isso nunca
    acontece (o pagamento leva de 30 s a dias). Se um canal inteiro passa a ter
    20%+ dos pedidos pagos "antes" de criados, não é ruído: é a integração
    gravando a compra em UTC enquanto o gateway grava o pagamento em horário
    local. A correção é por segmento, não por linha: pedidos pagos dias depois
    também estão deslocados, só não aparecem no sinal.
    """
    ped = ped.copy()
    lag = (ped["pago_em"] - ped["criado_em"]).dt.total_seconds()
    month = ped["criado_em"].dt.tz_convert(TZ_LOCAL).dt.strftime("%Y-%m")
    seg = pd.DataFrame({"canal": ped["canal_venda"], "mes": month, "neg": lag < 0, "pago": lag.notna()})
    stats = seg[seg["pago"]].groupby(["canal", "mes"]).agg(pedidos=("neg", "size"), negativos=("neg", "sum"))
    stats["share"] = stats["negativos"] / stats["pedidos"]
    bad = stats[(stats["share"] >= cfg["fuso_lag_negativo_min_share"]) & (stats["pedidos"] >= cfg["fuso_min_pedidos_segmento"])]
    hit = pd.Series(list(zip(seg["canal"], seg["mes"], strict=True)), index=ped.index).isin(set(bad.index))
    old = ped.loc[hit, "criado_em"]
    ped.loc[hit, "criado_em"] = old - pd.Timedelta(hours=3)
    log.add_many("pedidos", ped.loc[hit, "pedido_id"], "criado_em", "fuso_utc_segmento", "corrigido",
                 old.map(lambda t: t.isoformat()), ped.loc[hit, "criado_em"].map(lambda t: t.isoformat()))
    # Fora de um segmento: lag entre -3 h e 0 tem a assinatura exata do mesmo bug
    # (pedido da borda do mês, que o +3 h jogou para o mês seguinte) -> corrige
    # a linha. Qualquer outro lag negativo só é sinalizado.
    lag2 = (ped["pago_em"] - ped["criado_em"]).dt.total_seconds()
    lone = (lag2 < 0) & ~hit
    sig = lone & (lag2 >= -3 * 3600)
    old2 = ped.loc[sig, "criado_em"]
    ped.loc[sig, "criado_em"] = old2 - pd.Timedelta(hours=3)
    log.add_many("pedidos", ped.loc[sig, "pedido_id"], "criado_em", "fuso_utc_individual", "corrigido",
                 old2.map(lambda t: t.isoformat()), ped.loc[sig, "criado_em"].map(lambda t: t.isoformat()))
    log.add_many("pedidos", ped.loc[lone & ~sig, "pedido_id"], "pago_em", "pagamento_antes_da_compra", "sinalizado")
    return ped, stats.reset_index()


def fix_delivery_order(fr: pd.DataFrame, ped: pd.DataFrame, cfg: dict, log: TreatmentLog) -> pd.DataFrame:
    """Entrega antes da compra/postagem: tenta dia<->mês trocados e ano +1; senão anula.

    Um candidato só é aceito se cair entre a postagem (ou a compra) e
    ``entrega_max_dias`` depois. Sem candidato, ``entregue_em`` vira nulo e o
    pedido sai do cálculo de prazo de entrega, mas continua na receita: a
    venda aconteceu, só a data de entrega é desconhecida.
    """
    fr = fr.copy()
    criado = fr["pedido_id"].map(ped.drop_duplicates("pedido_id").set_index("pedido_id")["criado_em"])
    floor = fr["postado_em"].fillna(criado)
    bad = fr["entregue_em"].notna() & floor.notna() & (fr["entregue_em"] < floor)
    max_d = pd.Timedelta(days=cfg["entrega_max_dias"])
    fixed, nulled = [], []
    for i in fr.index[bad]:
        ts = fr.at[i, "entregue_em"].tz_convert(TZ_LOCAL)
        cands = []
        if ts.day <= 12:
            try:
                cands.append(ts.replace(month=ts.day, day=ts.month))
            except ValueError:
                pass
        cands.append(ts + pd.DateOffset(years=1))
        ok = [c.tz_convert("UTC") for c in cands if floor[i] <= c.tz_convert("UTC") <= floor[i] + max_d]
        if ok:
            fixed.append((i, fr.at[i, "entregue_em"], min(ok)))
        else:
            nulled.append((i, fr.at[i, "entregue_em"]))
    for i, _, new in fixed:
        fr.at[i, "entregue_em"] = new
    for i, _ in nulled:
        fr.at[i, "entregue_em"] = pd.NaT
    log.add_many("fretes", [fr.at[i, "frete_id"] for i, _, _ in fixed], "entregue_em", "entrega_antes_da_compra",
                 "corrigido", [o.isoformat() for _, o, _ in fixed], [n.isoformat() for _, _, n in fixed])
    log.add_many("fretes", [fr.at[i, "frete_id"] for i, _ in nulled], "entregue_em", "entrega_antes_da_compra",
                 "anulado", [o.isoformat() for _, o in nulled], [None] * len(nulled))
    return fr
