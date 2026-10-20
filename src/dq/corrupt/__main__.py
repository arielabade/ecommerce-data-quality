"""``python -m dq.corrupt``: gera gold, raw e o registro de defeitos.

Papel: orquestrar baseline -> eventos legítimos (gold) -> defeitos (raw).
Entrada: ``<paths.source>/*.parquet`` e ``config/corruption.yaml``.
Saída: ``<paths.gold>``, ``<paths.raw>`` (texto + ``_lote``) e
``<paths.defect_log>/defects.parquet``.
Dependências: ``dq.corrupt.*``.
"""

from __future__ import annotations

import shutil
import time

import numpy as np

from dq.corrupt.baseline import build_baseline
from dq.corrupt.defects import Injector
from dq.corrupt.formatting import to_raw
from dq.corrupt.legit import IdSeq, add_legit_events
from dq.corrupt.log import DefectLog
from dq.extract.source import load_source
from dq.settings import Settings, get_settings


def run(settings: Settings | None = None) -> dict:
    s = settings or get_settings()
    cfg = s.corruption
    t0 = time.time()
    base = build_baseline(load_source(s))
    log = DefectLog()
    rng = np.random.default_rng(int(cfg["legitimos"]["seed"]))  # a verdade não depende do seed da sujeira
    legit = add_legit_events(base, cfg, log, rng, IdSeq(base))
    gold = base

    inj = Injector(gold, cfg, legit, log)
    dirty = inj.run()
    raw = to_raw(dirty, cfg, log, getattr(inj, "tz_shifted", set()))

    for key, tables in (("gold", gold), ("raw", raw)):
        out = s.path(key)
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir(parents=True)
        for name, df in tables.items():
            df.to_parquet(out / f"{name}.parquet", index=False)
    df = log.write(s.path("defect_log"))
    print(f"gold e raw gravados em {time.time() - t0:.0f}s")
    print(df.groupby(["tabela", "tipo_defeito", "legitimo"]).size().to_string())
    return {"defeitos": int((~df["legitimo"]).sum()), "legitimos": int(df["legitimo"].sum())}


if __name__ == "__main__":
    run()
