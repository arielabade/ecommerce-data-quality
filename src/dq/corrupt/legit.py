"""Outliers verdadeiros: eventos reais que parecem erro.

Papel: acrescentar à base de referência três eventos de negócio legítimos e
marcá-los no registro como ``legitimo=True``. Eles existem para medir quantos
acertos de verdade cada técnica de detecção derruba por engano.
- Black Friday: já existe na origem; os pedidos dos dias da promoção são marcados.
- Cliente B2B: uma confecção que compra rolos em volume, com tickets dezenas
  de vezes acima da mediana, e algumas reposições no mesmo dia.
- Produto viral: uma chita estampada que viraliza por 10 dias em junho.
Entrada: base de referência (``baseline.build_baseline``) e
``corruption.yaml: legitimos``.
Saída: as tabelas com os pedidos/itens/fretes novos (que passam a fazer parte
do gold) e as linhas ``legitimo_*`` no ``DefectLog``.
Dependências: numpy, pandas.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from dq.corrupt.baseline import TZ_LOCAL
from dq.corrupt.log import DefectLog


class IdSeq:
    """Próximos ids livres por tabela (novos registros não colidem com a origem)."""

    def __init__(self, t: dict[str, pd.DataFrame]):
        self.next = {
            "pedidos": int(t["pedidos"]["pedido_id"].max()) + 1,
            "numero": int(t["pedidos"]["numero_pedido"].astype(int).max()) + 1,
            "itens_pedido": int(t["itens_pedido"]["item_pedido_id"].max()) + 1,
            "fretes": int(t["fretes"]["frete_id"].max()) + 1,
            "clientes": int(t["clientes"]["cliente_id"].max()) + 1,
        }

    def take(self, key: str, n: int = 1) -> np.ndarray:
        start = self.next[key]
        self.next[key] = start + n
        return np.arange(start, start + n)


def _local(ts: pd.Timestamp) -> pd.Timestamp:
    return ts.tz_localize(TZ_LOCAL).tz_convert("UTC")


def _append(t: dict[str, pd.DataFrame], name: str, rows: list[dict]) -> None:
    if not rows:
        return
    new = pd.DataFrame(rows)
    base = t[name]
    new = new.reindex(columns=base.columns)
    t[name] = pd.concat([base, new.astype(base.dtypes.to_dict(), errors="ignore")], ignore_index=True)


def make_order(ids: IdSeq, *, cliente_id: int, endereco_id, criado_utc: pd.Timestamp, itens: list[tuple],
               variantes: pd.DataFrame, status: str, canal: str, ip: str, rng: np.random.Generator,
               desconto_pct: float = 0.0, frete_centavos: int | None = None, transportadora: str = "Correios",
               servico: str = "PAC", peso_kg: float | None = None,
               custo_centavos: int | None = None) -> tuple[dict, list[dict], dict]:
    """Monta um pedido coerente (cabeçalho, itens, frete) a partir de (variante_id, quantidade)."""
    pid = int(ids.take("pedidos")[0])
    vv = variantes.set_index("variante_id")
    item_rows, subtotal, metros = [], 0, 0.0
    for variante_id, qtd in itens:
        v = vv.loc[variante_id]
        preco = int(v["preco_atual_centavos"])
        tot = int(round(preco * qtd))
        subtotal += tot
        metros += qtd * float(v["metros_por_unidade"])
        item_rows.append({
            "item_pedido_id": int(ids.take("itens_pedido")[0]), "pedido_id": pid, "variante_id": int(variante_id),
            "quantidade": round(float(qtd), 2), "preco_unitario_centavos": preco, "preco_tabela_centavos": preco,
            "custo_unitario_centavos": int(v["custo_atual_centavos"]), "total_item_centavos": tot,
        })
    peso = peso_kg if peso_kg is not None else round(0.12 * metros + 0.1, 3)
    if frete_centavos is None:
        frete_centavos = int(round((12 + 2.4 * peso ** 0.85) * 100))
    gratis = int(frete_centavos == 0)
    desconto = int(round(subtotal * desconto_pct))
    total = subtotal - desconto + frete_centavos
    pago = status in ("pago", "enviado", "entregue", "devolvido")
    pago_em = criado_utc + pd.Timedelta(minutes=float(rng.uniform(2, 40))) if pago else pd.NaT
    cancel = criado_utc + pd.Timedelta(hours=float(rng.uniform(1, 30))) if status == "cancelado" else pd.NaT
    header = {
        "pedido_id": pid, "numero_pedido": str(int(ids.take("numero")[0])), "cliente_id": int(cliente_id),
        "endereco_entrega_id": endereco_id, "cupom_id": pd.NA, "canal_venda": canal, "status": status,
        "criado_em": criado_utc.floor("s"), "pago_em": pago_em.floor("s") if pago else pd.NaT,
        "cancelado_em": cancel.floor("s") if status == "cancelado" else pd.NaT,
        "subtotal_centavos": subtotal, "desconto_centavos": desconto, "frete_centavos": frete_centavos,
        "total_centavos": total, "ip_checkout": ip,
    }
    postado = pago_em + pd.Timedelta(hours=float(rng.uniform(18, 50))) if status in ("enviado", "entregue") else pd.NaT
    prazo = int(rng.integers(4, 11))
    entregue = postado + pd.Timedelta(days=float(prazo + rng.normal(0, 1.2))) if status == "entregue" else pd.NaT
    frete = {
        "frete_id": int(ids.take("fretes")[0]), "pedido_id": pid, "transportadora": transportadora,
        "servico": servico, "peso_kg": peso, "valor_cobrado_centavos": frete_centavos,
        "custo_real_centavos": custo_centavos if custo_centavos is not None else
        int(round((frete_centavos if not gratis else 2200) * rng.uniform(0.8, 1.05))),
        "frete_gratis": gratis, "prazo_estimado_dias": prazo,
        "postado_em": postado.floor("s") if pd.notna(postado) else pd.NaT,
        "entregue_em": entregue.floor("s") if pd.notna(entregue) else pd.NaT,
    }
    return header, item_rows, frete


class FreightDonor:
    """Tarifa real: frete e custo de um pedido da origem com a mesma UF, serviço e peso mais próximo.

    Os pedidos legítimos novos precisam obedecer à tabela da transportadora;
    um frete sorteado ao acaso viraria "outlier" por construção.
    """

    def __init__(self, t: dict[str, pd.DataFrame]):
        f = t["fretes"].merge(t["pedidos"][["pedido_id", "endereco_entrega_id"]], on="pedido_id")
        f = f.merge(t["enderecos"][["endereco_id", "uf"]], left_on="endereco_entrega_id", right_on="endereco_id")
        f = f[(f["valor_cobrado_centavos"] > 0) & f["servico"].isin(["PAC", "Jadlog .Package"])]
        self.groups = {k: g.sort_values("peso_kg")[["peso_kg", "valor_cobrado_centavos", "custo_real_centavos"]].to_numpy()
                       for k, g in f.groupby(["uf", "servico"])}

    def quote(self, uf, servico: str, peso: float) -> tuple[int, int] | None:
        g = self.groups.get((uf, servico))
        if g is None or not len(g):
            return None
        i = min(int(np.searchsorted(g[:, 0], peso)), len(g) - 1)
        return int(g[i, 1]), int(g[i, 2])


def add_legit_events(t: dict[str, pd.DataFrame], cfg: dict, log: DefectLog, rng: np.random.Generator,
                     ids: IdSeq) -> set[int]:
    """Acrescenta os eventos legítimos e devolve os pedido_id envolvidos."""
    legit: set[int] = set()
    lc = cfg["legitimos"]
    ped, var = t["pedidos"], t["variantes"]

    # --- Black Friday (já na origem): marca pedidos e dias -------------------
    if lc.get("black_friday"):
        cal = t["calendario_comercial"]
        bf_days = set(cal.loc[cal["nome_promocao"].fillna("").str.contains("Black Friday"), "data"])
        local_day = ped["criado_em"].dt.tz_convert(TZ_LOCAL).dt.date
        bf = ped.loc[local_day.isin(bf_days), "pedido_id"]
        log.add_many("pedidos", bf, "*", "legitimo_black_friday", legitimo=True, pedido_refs=bf)
        log.add_many("serie_diaria", sorted(str(d) for d in bf_days), "receita", "legitimo_black_friday", legitimo=True)
        legit |= set(int(x) for x in bf)

    # --- Cliente B2B: confecção que compra rolos em volume -------------------
    b2b_cfg = lc["b2b"]
    cli = t["clientes"]
    paid = ped[ped["status"].isin(["pago", "enviado", "entregue"])]
    cand = cli[(cli["tipo_cliente"] == "confeccao") & (cli["tipo_pessoa"] == "PJ")]["cliente_id"]
    counts = paid[paid["cliente_id"].isin(cand)].groupby("cliente_id").size().sort_values(ascending=False)
    b2b_id = int(counts.index[0]) if len(counts) else int(cand.iloc[0])
    end = t["enderecos"]
    b2b_end = end[(end["cliente_id"] == b2b_id)]["endereco_id"]
    b2b_end = int(b2b_end.iloc[0]) if len(b2b_end) else pd.NA
    rolos = var[(var["formato_venda"] == "rolo") & (var["ativo"] == 1)]["variante_id"].to_numpy()
    start, stop = pd.Timestamp(b2b_cfg["inicio"]), pd.Timestamp(b2b_cfg["fim"])
    n = int(b2b_cfg["pedidos"])
    days = np.sort(rng.choice(pd.date_range(start, stop, freq="D"), size=n, replace=False))
    lo, hi = b2b_cfg["rolos_por_item"]
    headers, items, fretes = [], [], []
    b2b_ip = "200.155.71.20"
    same_day = set(rng.choice(n, size=min(int(b2b_cfg["reposicoes_mesmo_dia"]), n), replace=False).tolist())
    for i, d in enumerate(days):
        moments = [pd.Timestamp(d) + pd.Timedelta(hours=float(rng.uniform(9, 15)))]
        if i in same_day:  # reposição legítima ~1 h depois, itens diferentes
            moments.append(moments[0] + pd.Timedelta(minutes=float(rng.uniform(40, 95))))
        for m in moments:
            k = int(rng.integers(2, 5))
            chosen = rng.choice(rolos, size=k, replace=False)
            its = [(int(v), int(rng.integers(lo, hi + 1))) for v in chosen]
            h, it, f = make_order(ids, cliente_id=b2b_id, endereco_id=b2b_end, criado_utc=_local(m), itens=its,
                                  variantes=var, status="entregue", canal=str(rng.choice(["site", "whatsapp"])),
                                  ip=b2b_ip, rng=rng, desconto_pct=0.08, transportadora="Jadlog",
                                  servico="Jadlog .Com")
            headers.append(h)
            items += it
            fretes.append(f)
    _append(t, "pedidos", headers)
    _append(t, "itens_pedido", items)
    _append(t, "fretes", fretes)
    b2b_ids = [h["pedido_id"] for h in headers]
    log.add_many("pedidos", b2b_ids, "*", "legitimo_b2b", legitimo=True, pedido_refs=b2b_ids,
                 detalhe=f"cliente {b2b_id}")
    legit |= set(b2b_ids)

    # --- Produto viral: chita estampada por 10 dias --------------------------
    vc = lc["viral"]
    prods = t["produtos"]
    chita = prods[prods["categoria"] == "Chita"]["produto_id"]
    pool = var[var["produto_id"].isin(chita) & (var["formato_venda"] == "metro") & (var["estampa"] != "Liso")]
    viral_var = int(pool.sort_values("variante_id").iloc[len(pool) // 2]["variante_id"])
    metro = var[(var["formato_venda"] == "metro") & (var["ativo"] == 1)]["variante_id"].to_numpy()
    daily_mean = len(ped) / ped["criado_em"].dt.tz_convert(TZ_LOCAL).dt.date.nunique()
    per_day = max(3, int(round(daily_mean * float(vc["pedidos_extra_sobre_media_diaria"]))))
    buyers = cli[cli["tipo_cliente"].isin(["hobby", "costureira"])]["cliente_id"].to_numpy()
    principal = end[end["principal"] == 1].drop_duplicates("cliente_id").set_index("cliente_id")["endereco_id"]
    statuses = ped["status"].value_counts(normalize=True)
    headers, items, fretes, viral_days = [], [], [], []
    donor = FreightDonor(t)
    uf_of = end.set_index("endereco_id")["uf"].to_dict()
    prices = var.set_index("variante_id")["preco_atual_centavos"]
    mpu = var.set_index("variante_id")["metros_por_unidade"]
    for d in pd.date_range(vc["inicio"], periods=int(vc["dias"]), freq="D"):
        viral_days.append(str(d.date()))
        # pico no meio do período, como um vídeo que espalha e depois esfria
        bump = 0.6 + 0.8 * np.sin(np.pi * (len(viral_days) - 0.5) / int(vc["dias"]))
        for _ in range(int(per_day * bump)):
            c = int(rng.choice(buyers))
            m = pd.Timestamp(d) + pd.Timedelta(minutes=float(rng.uniform(7 * 60, 23.9 * 60)))
            its = [(viral_var, float(rng.choice([1.5, 2, 2.5, 3, 4, 5, 6])))]
            if rng.random() < 0.4:
                its.append((int(rng.choice(metro)), float(rng.choice([1, 1.5, 2, 3]))))
            st = str(rng.choice(statuses.index, p=statuses.to_numpy()))
            sub = sum(int(prices.loc[v] * q) for v, q in its)
            metros = sum(q * float(mpu.loc[v]) for v, q in its)
            peso = round(0.12 * metros + 0.1, 3)
            serv = "PAC" if rng.random() < 0.7 else "Jadlog .Package"
            end_id = principal.get(c, pd.NA)
            q = donor.quote(uf_of.get(end_id), serv, peso) if pd.notna(end_id) else None
            fc, custo = (0, q[1]) if (q and sub >= 19900) else (q if q else (0, None))
            h, it, f = make_order(ids, cliente_id=c, endereco_id=end_id, criado_utc=_local(m),
                                  itens=its, variantes=var, status=st, canal="site",
                                  ip=f"189.{rng.integers(1, 255)}.{rng.integers(1, 255)}.{rng.integers(1, 255)}",
                                  rng=rng, frete_centavos=fc, peso_kg=peso, servico=serv,
                                  transportadora="Correios" if serv == "PAC" else "Jadlog", custo_centavos=custo)
            headers.append(h)
            items += it
            fretes.append(f)
    _append(t, "pedidos", headers)
    _append(t, "itens_pedido", items)
    _append(t, "fretes", fretes)
    viral_ids = [h["pedido_id"] for h in headers]
    log.add_many("pedidos", viral_ids, "*", "legitimo_viral", legitimo=True, pedido_refs=viral_ids,
                 detalhe=f"variante {viral_var}")
    log.add_many("serie_diaria", viral_days, "receita", "legitimo_viral", legitimo=True)
    legit |= set(viral_ids)
    return legit
