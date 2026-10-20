"""Relatório de qualidade de cada execução do ETL.

Papel: responder, para cada execução, três perguntas que o time de negócio
faz: o que entrou, o que foi corrigido (e como) e o que ficou de fora (e
quanto dinheiro isso representa).
Entrada: dicionário montado por ``dq.etl.run_once``.
Saída: ``<paths.runs>/<run_id>/quality_report.json`` e ``.md``; a última
execução também vai para ``<paths.reports>/quality_report.md``.
Dependências: pandas. Não lê o registro de defeitos: este relatório é o que o
ETL sabe sozinho, sem gabarito.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from dq.settings import Settings

ACTION_PT = {"removido": "removidos", "corrigido": "corrigidos", "limitado": "limitados", "imputado": "imputados",
             "anulado": "anulados", "sinalizado": "sinalizados", "quarentena": "em quarentena",
             "padronizado": "padronizados"}


def run_dir(s: Settings, run_id: str) -> Path:
    d = s.path("runs") / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def treatment_summary(log: pd.DataFrame) -> list[dict]:
    if log.empty:
        return []
    g = log.groupby(["tabela", "regra", "acao"]).size().rename("linhas").reset_index()
    return g.sort_values(["acao", "linhas"], ascending=[True, False]).to_dict("records")


def excluded_summary(excluded: pd.DataFrame, staged_orders: pd.DataFrame) -> list[dict]:
    if excluded.empty:
        return []
    tot = staged_orders.drop_duplicates("pedido_id").set_index("pedido_id")["total_centavos"]
    paid = staged_orders.drop_duplicates("pedido_id").set_index("pedido_id")["status"].isin(
        ["pago", "enviado", "entregue", "devolvido"])
    e = excluded.assign(total=excluded["pedido_id"].map(tot), pago=excluded["pedido_id"].map(paid))
    g = e.groupby("motivo").agg(pedidos=("pedido_id", "size"),
                                receita_paga_reais=("total", lambda x: float(x[e.loc[x.index, "pago"]].sum()) / 100))
    return g.reset_index().to_dict("records")


def quarantine_by_reason(q: pd.DataFrame) -> list[dict]:
    if q.empty:
        return []
    g = q.groupby(["tabela", "motivo"]).agg(linhas=("chave", "size"),
                                           valor_reais=("valor_centavos", lambda x: float(x.sum()) / 100))
    return g.reset_index().to_dict("records")


def tz_segments(stats: pd.DataFrame, cfg: dict) -> list[dict]:
    bad = stats[(stats["share"] >= cfg["fuso_lag_negativo_min_share"]) & (stats["pedidos"] >= cfg["fuso_min_pedidos_segmento"])]
    return bad.assign(share=bad["share"].round(3)).to_dict("records")


def _default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if np.isnan(o) else float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, set):
        return sorted(o)
    return str(o)


def br(v, nd: int = 0) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    return f"{v:,.{nd}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def to_markdown(rep: dict) -> str:
    m = rep["metricas"]
    lines = [
        f"# Relatório de qualidade — execução `{rep['run_id']}`",
        "",
        f"Corte: **{rep['corte']}** · lotes até **{rep['lotes_ate']}** · {rep['segundos']} s · "
        f"idempotente nesta execução: **{'sim' if rep['idempotente'] else 'não (houve mudança)'}**",
        "",
        "## O que entrou",
        "",
        "| Tabela | Linhas no raw | Inseridas | Atualizadas | Removidas | Inalteradas |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for t, c in rep["carga"].items():
        lines.append(f"| `{t}` | {br(rep['entrada'].get(t, 0))} | {br(c['inseridos'])} | {br(c['atualizados'])} | "
                     f"{br(c['removidos'])} | {br(c['inalterados'])} |")
    lines += ["", "## Contratos", "", "| Camada | Resultado |", "| --- | --- |"]
    for layer, c in rep["contratos"].items():
        lines.append(f"| {layer} | {'ok' if c['ok'] else 'FALHOU'} ({len(c['tabelas'])} tabelas) |")
    lines += ["", "## O que foi tratado", "", "| Tabela | Regra | Ação | Linhas |", "| --- | --- | --- | ---: |"]
    for r in rep["tratamento"]:
        lines.append(f"| `{r['tabela']}` | `{r['regra']}` | {r['acao']} | {br(r['linhas'])} |")
    lines += ["", "## O que ficou de fora das métricas", "", "### Excluídos (não eram vendas)", "",
              "| Motivo | Pedidos | Receita paga que inflaria a métrica |", "| --- | ---: | ---: |"]
    for r in rep["excluidos"]:
        lines.append(f"| {r['motivo']} | {br(r['pedidos'])} | R$ {br(r['receita_paga_reais'], 2)} |")
    lines += ["", f"### Quarentena ({br(rep['quarentena']['em_quarentena'])} registros; "
              f"{br(rep['quarentena']['entraram'])} entraram e {br(rep['quarentena']['liberados'])} foram liberados nesta execução)", "",
              "| Tabela | Motivo | Linhas | Valor parado |", "| --- | --- | ---: | ---: |"]
    for r in rep["quarentena"]["por_motivo"]:
        lines.append(f"| `{r['tabela']}` | {r['motivo']} | {br(r['linhas'])} | R$ {br(r['valor_reais'], 2)} |")
    nd = rep["diagnosticos"]["nulos_custo_real"]
    by_carrier = ", ".join(f"{k} {br(v, 1)}%" for k, v in nd["por_transportadora_pct"].items())
    segs = ", ".join(f"{r['canal']} {r['mes']} ({br(r['share'] * 100, 0)}% pagos antes de criados)"
                     for r in rep["diagnosticos"]["fuso_segmentos_corrigidos"]) or "nenhum"
    lines += ["", "## Diagnósticos", "",
              f"- **Nulos em `fretes.custo_real`:** {br(nd['ausentes'])} ({br(nd['ausentes_pct'], 1)}%). "
              f"Por transportadora: {by_carrier}. Leitura: {nd['leitura']}.",
              f"- **Segmentos com fuso trocado:** {segs}.",
              f"- **Marts:** {rep['marts']['modo']}"
              + (f" de {rep['marts']['datas']} dias e {rep['marts']['meses']} meses" if rep['marts']['modo'] == 'reprocessamento' else ""),
              "", "## Métricas depois da limpeza", "",
              f"Receita paga R$ {br(m['receita'], 2)} · {br(m['pedidos_pagos'])} pedidos pagos · ticket médio "
              f"R$ {br(m['ticket_medio'], 2)} · cancelamento {br(m['taxa_cancelamento'] * 100, 2)}% · recompra "
              f"{br(m['taxa_recompra'] * 100, 1)}% dos clientes", ""]
    return "\n".join(lines)


def write(s: Settings, rep: dict) -> None:
    d = run_dir(s, rep["run_id"])
    (d / "quality_report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=_default), encoding="utf-8")
    md = to_markdown(rep)
    (d / "quality_report.md").write_text(md, encoding="utf-8")
    out = s.path("reports")
    out.mkdir(parents=True, exist_ok=True)
    (out / "quality_report.md").write_text(md, encoding="utf-8")
