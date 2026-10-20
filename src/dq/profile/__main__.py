"""``python -m dq.profile``: perfil da origem (e do raw, se já existir).

Papel: produzir ``reports/profile/source.json`` e ``docs/DATA_SOURCE.md``; se o
raw já foi gerado, também ``reports/profile/raw.json`` (base suja como o ETL a
recebe).
Entrada: ``<paths.source>`` e, opcionalmente, ``<paths.raw>``.
Saída: JSON de perfil e o Markdown da fonte.
Dependências: ``dq.profile.profiler``, ``dq.profile.markdown``.
"""

from __future__ import annotations

import json

import pandas as pd

from dq.extract.source import load_source
from dq.profile.markdown import render
from dq.profile.profiler import profile_tables, to_jsonable
from dq.settings import ROOT, get_settings


def br(v: float, nd: int = 0) -> str:
    """Número no formato brasileiro: 1.234,56."""
    return f"{v:,.{nd}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def business_facts(t: dict[str, pd.DataFrame]) -> dict:
    """Fatos da origem que viram regras de limpeza ou premissas da análise."""
    p, it, f = t["pedidos"], t["itens_pedido"], t["fretes"]
    p = p[p["pedido_teste"] == 0]
    sub = it.groupby("pedido_id")["total_item"].sum().round(2)
    m = p.merge(f[["pedido_id", "valor_cobrado", "peso_kg"]], on="pedido_id", how="left")
    ident = {
        "`total = subtotal - desconto + frete`": ((p["subtotal"] - p["desconto"] + p["frete"] - p["total"]).abs() < 0.005).mean(),
        "`subtotal = soma(itens.total_item)`": ((p.set_index("pedido_id")["subtotal"] - sub).abs().dropna() < 0.005).mean(),
        "`total_item = quantidade x preco_unitario`": ((it["quantidade"] * it["preco_unitario"] - it["total_item"]).abs() < 0.011).mean(),
        "`pedidos.frete = fretes.valor_cobrado`": ((m["frete"] - m["valor_cobrado"]).abs() < 0.005).mean(),
        "`desconto <= subtotal`": (p["desconto"] <= p["subtotal"]).mean(),
        "`preco_unitario` entre 55% e 100% de `preco_tabela`": (it["preco_unitario"] / it["preco_tabela"]).between(0.55, 1.0).mean(),
        "`entregue_em >= postado_em`": (pd.to_datetime(f["entregue_em"]) >= pd.to_datetime(f["postado_em"]))[
            f["entregue_em"].notna() & f["postado_em"].notna()].mean(),
    }
    identidades = [f"{k}: {br(v * 100, 2)}% das linhas" for k, v in ident.items()]

    paid = p[p["status"].isin(["pago", "enviado", "entregue", "devolvido"])].copy()
    paid["dia"] = pd.to_datetime(paid["criado_em"]).dt.date
    daily = paid.groupby("dia").agg(pedidos=("pedido_id", "size"), receita=("total", "sum"))
    cal = t["calendario_comercial"].assign(data=lambda d: pd.to_datetime(d["data"]).dt.date).set_index("data")
    daily = daily.join(cal["nome_promocao"])
    bf = daily[daily["nome_promocao"].fillna("").str.contains("Black")]
    top_bf = bf.sort_values("pedidos").iloc[-1]
    q1, q3 = daily["receita"].quantile([0.25, 0.75])
    status = p["status"].value_counts(normalize=True)
    lag = (pd.to_datetime(p["pago_em"]) - pd.to_datetime(p["criado_em"])).dt.total_seconds() / 60
    fr_kg = (m["frete"] / m["peso_kg"]).replace([float("inf")], float("nan"))
    cli_orders = paid.groupby("cliente_id").size()
    achados = [
        f"**Volume:** {br(len(p))} pedidos reais, {br(len(it))} itens, {br(t['clientes'].shape[0])} clientes. "
        f"Média de {br(daily['pedidos'].mean(), 0)} pedidos pagos por dia (mín {daily['pedidos'].min()}, máx {daily['pedidos'].max()}).",
        f"**Status:** {br(status.get('entregue', 0) * 100, 1)}% entregue, {br(status.get('cancelado', 0) * 100, 1)}% cancelado, "
        f"{br(status.get('devolvido', 0) * 100, 1)}% devolvido. Taxa de cancelamento é métrica de negócio, não erro.",
        f"**Ticket:** mediana R$ {br(p['total'].median(), 2)}, p99 R$ {br(p['total'].quantile(0.99), 2)}, máximo R$ {br(p['total'].max(), 2)}; "
        f"assimetria {br(p['total'].skew(), 1)}. Cauda longa à direita: compras em rolo de ateliês e confecções.",
        f"**Sazonalidade:** a Black Friday é o maior pico. O dia mais forte ({top_bf.name}) teve {int(top_bf['pedidos'])} pedidos "
        f"({br(top_bf['pedidos'] / daily['pedidos'].mean(), 1)}x a média) e R$ {br(top_bf['receita'], 0)} de receita. "
        f"O limite superior do IQR da receita diária é R$ {br(q3 + 1.5 * (q3 - q1), 0)}: um corte por IQR global já o trata como erro.",
        f"**Pagamento:** {(lag < 0).sum()} pedidos com `pago_em` antes de `criado_em`; mediana de {br(lag.median(), 1)} min entre criar e pagar. "
        "Um lag negativo só pode ser fuso misturado.",
        f"**Frete:** mediana de R$ {br(fr_kg.median(), 2)} por kg (p99 R$ {br(fr_kg.quantile(0.99), 2)}); "
        f"{br((m['frete'] == 0).mean() * 100, 1)}% dos pedidos com frete zero (frete grátis ou retirada).",
        f"**Recompra:** {br((cli_orders >= 2).mean() * 100, 1)}% dos clientes com pedido pago compraram 2+ vezes.",
        f"**Fretes:** `entregue_em` nulo em {br(f['entregue_em'].isna().mean() * 100, 1)}% (não entregues + ~2% \"entregue sem baixa\", "
        f"imperfeição nativa 22); `custo_real` nulo em {br(f['custo_real'].isna().mean() * 100, 1)}%.",
    ]
    return {"identidades": identidades, "achados": achados}


def main() -> None:
    s = get_settings()
    out = s.path("reports") / "profile"
    out.mkdir(parents=True, exist_ok=True)
    src = load_source(s)
    prof = to_jsonable(profile_tables(src))
    (out / "source.json").write_text(json.dumps(prof, ensure_ascii=False, indent=1), encoding="utf-8")
    md = render(prof, business_facts(src))
    target = ROOT / "docs" / "DATA_SOURCE.md" if s.profile == "default" else out / "DATA_SOURCE.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(md, encoding="utf-8")
    print(f"perfil da origem -> {target}")
    raw_dir = s.path("raw")
    if (raw_dir / "pedidos.parquet").exists():
        raw = {p.stem: pd.read_parquet(p) for p in sorted(raw_dir.glob("*.parquet"))}
        (out / "raw.json").write_text(json.dumps(to_jsonable(profile_tables(raw)), ensure_ascii=False, indent=1),
                                      encoding="utf-8")
        print(f"perfil do raw -> {out / 'raw.json'}")


if __name__ == "__main__":
    main()
