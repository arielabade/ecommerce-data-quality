"""``python -m dq.report``: dashboard estático e figuras do README.

Papel: publicar a avaliação. O relatório de qualidade por execução já é
gravado pelo próprio ETL (``dq.report.quality``); aqui se gera o que é para
leitura humana fora do terminal.
Entrada: ``<paths.reports>/evaluation.json`` e ``<paths.runs>``.
Saída: ``<paths.dashboard>/index.html`` e ``<paths.figures>/*.svg``.
Dependências: ``dq.report.dashboard``, ``dq.report.figures``.
"""

from __future__ import annotations

import json

from dq.report.dashboard import write_dashboard
from dq.report.figures import write_figures
from dq.settings import get_settings


def main(settings=None) -> None:
    s = settings or get_settings()
    page = write_dashboard(s)
    ev = json.loads((s.path("reports") / "evaluation.json").read_text(encoding="utf-8"))
    figs = write_figures(ev, s.path("figures"))
    print(f"dashboard -> {page}")
    for f in figs:
        print(f"figura -> {f}")


if __name__ == "__main__":
    main()
