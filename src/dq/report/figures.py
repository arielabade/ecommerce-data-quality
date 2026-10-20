"""Figuras do README (SVG), no tema escuro dos painéis ABADE.

Papel: três figuras estáticas que o GitHub mostra sem JavaScript: erro mensal
antes x depois, precisão x recall por técnica e o custo do IQR global.
Entrada: ``evaluation.json``.
Saída: ``<paths.figures>/*.svg``.
Dependências: matplotlib.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

CARBON, IVORY, STEEL, GRID = "#050505", "#F6F5F0", "#9BA2AA", "#24262B"
COBALT, AMBER, TEAL, CRIMSON = "#5B6CFF", "#BD8620", "#1F9B73", "#D9485F"


def _style():
    plt.rcParams.update({
        "figure.facecolor": CARBON, "axes.facecolor": CARBON, "savefig.facecolor": CARBON,
        "axes.edgecolor": GRID, "axes.labelcolor": STEEL, "xtick.color": STEEL, "ytick.color": STEEL,
        "text.color": IVORY, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
        "axes.spines.top": False, "axes.spines.right": False, "font.family": ["Lato", "DejaVu Sans"],
        "font.size": 11, "svg.fonttype": "path", "legend.frameon": False,
    })


def _br(v: float, nd: int = 1) -> str:
    return f"{v:,.{nd}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _title(fig, title: str, sub: str) -> None:
    fig.text(0.04, 0.95, title, fontsize=15, fontweight="bold", color=IVORY, va="top")
    fig.text(0.04, 0.885, sub, fontsize=10.5, color=STEEL, va="top")


def monthly_error(ev: dict, out: Path) -> None:
    m = pd.DataFrame(ev["mensal"]).pivot(index="mes", columns="versao", values="receita")
    e = pd.DataFrame({"antes": m["antes"] / m["verdade"] - 1, "depois": m["depois"] / m["verdade"] - 1}) * 100
    x = pd.to_datetime(e.index)
    fig, ax = plt.subplots(figsize=(11, 4.6))
    fig.subplots_adjust(top=0.78, bottom=0.12, left=0.07, right=0.84)
    ax.axhline(0, color=STEEL, lw=1)
    ax.plot(x, e["antes"], color=AMBER, lw=2, marker="o", ms=3.5)
    ax.plot(x, e["depois"], color=COBALT, lw=2, marker="o", ms=3.5)
    ax.annotate("antes da limpeza", (x[-1], e["antes"].iloc[-1]), xytext=(8, 0), textcoords="offset points",
                color=AMBER, va="center", fontweight="bold")
    ax.annotate("depois", (x[-1], e["depois"].iloc[-1]), xytext=(8, 0), textcoords="offset points",
                color=COBALT, va="center", fontweight="bold")
    ax.set_ylabel("erro da receita mensal (%)")
    ax.yaxis.set_major_formatter(lambda v, _: f"{v:+.0f}%")
    _title(fig, f"Receita mensal: erro de {_br(e['antes'].min())}% a +{_br(e['antes'].max())}% antes; "
                f"de {_br(e['depois'].min())}% a {_br(e['depois'].max())}% depois",
           "Receita paga de cada mês contra a verdade (zero = número certo). Dados simulados.")
    fig.savefig(out / "monthly_error.svg")
    plt.close(fig)


def techniques(ev: dict, out: Path) -> None:
    rows = pd.DataFrame(ev["deteccao"]["valores_pedido"])
    rows = rows.iloc[::-1]
    fig, ax = plt.subplots(figsize=(11, 5.2))
    fig.subplots_adjust(top=0.8, bottom=0.1, left=0.33, right=0.97)
    y = range(len(rows))
    ax.barh([i + 0.2 for i in y], rows["precisao"] * 100, height=0.38, color=COBALT, label="precisão")
    ax.barh([i - 0.2 for i in y], rows["recall"] * 100, height=0.38, color=AMBER, label="recall")
    ax.set_yticks(list(y), [f"{t}\n{int(l):,} legítimos derrubados".replace(",", ".") for t, l in
                            zip(rows["tecnica"], rows["legitimos_derrubados"], strict=True)], fontsize=9.5)
    ax.set_xlim(0, 105)
    ax.xaxis.set_major_formatter(lambda v, _: f"{v:.0f}%")
    ax.grid(axis="y", visible=False)
    ax.legend(loc="lower right", bbox_to_anchor=(1.0, 1.0), ncols=2)
    combo = rows[rows["tecnica"].str.contains("ETL")].iloc[0]
    iqr = rows[rows["tecnica"] == "IQR global"].iloc[0]
    _title(fig, f"Regras + modelo de frete: {_br(combo['recall'] * 100, 0)}% de recall, {int(combo['legitimos_derrubados'])} legítimos marcados; "
                f"IQR global: {int(iqr['legitimos_derrubados']):,} marcados".replace(",", "."),
           "Erros de valor em pedidos (frete, desconto, total), medidos contra o registro de defeitos.")
    fig.savefig(out / "techniques.svg")
    plt.close(fig)


def wrong_decision(ev: dict, out: Path) -> None:
    d = pd.DataFrame(ev["decisao_errada"]["remover_dias_iqr"]["por_mes"])
    d = d[(d["mes"] >= "2025-01") & (d["mes"] <= "2025-12")]
    ev_ = ev["decisao_errada"].get("eventos_legitimos", {})
    fig, ax = plt.subplots(figsize=(11, 4.4))
    fig.subplots_adjust(top=0.78, bottom=0.12, left=0.08, right=0.97)
    x = range(len(d))
    ax.bar([i - 0.2 for i in x], d["limpo"] / 1e6, width=0.38, color=COBALT, label="base limpa")
    ax.bar([i + 0.2 for i in x], d["decisao_errada"] / 1e6, width=0.38, color=AMBER, label="apagando dias acima do IQR")
    ax.set_xticks(list(x), [m[5:] + "/25" for m in d["mes"]])
    ax.set_ylabel("receita (R$ milhões)")
    ax.grid(axis="x", visible=False)
    ax.legend(loc="lower right", bbox_to_anchor=(1.0, 1.0), ncols=2)
    nov = ev["decisao_errada"]["remover_dias_iqr"]["novembros"].get("2025-11", 0)
    bf = ev_.get("black_friday", {}).get("removida_dias_iqr", 0)
    b2b = ev_.get("b2b", {}).get("removida_dias_iqr", 0)
    _title(fig, f"Apagar \"dias anômalos\" pelo IQR global subestima novembro/2025 em {_br(abs(nov) * 100)}%",
           f"E some com {_br(bf * 100, 0)}% da receita da Black Friday e {_br(b2b * 100, 0)}% da do maior cliente B2B. Dados simulados.")
    fig.savefig(out / "wrong_decision.svg")
    plt.close(fig)


def write_figures(ev: dict, out: Path) -> list[Path]:
    _style()
    out.mkdir(parents=True, exist_ok=True)
    monthly_error(ev, out)
    techniques(ev, out)
    wrong_decision(ev, out)
    return sorted(out.glob("*.svg"))
