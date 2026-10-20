"""Amostra pequena e versionada da origem (perfil ``small``).

Papel: permitir que o CI e o teste de ponta a ponta rodem sem MySQL.
Entrada: a extração completa em ``data/source``.
Saída: ``data/sample/<tabela>.parquet`` com uma fração dos clientes e todos os
pedidos, itens, fretes e endereços deles; catálogo e calendário inteiros.
Dependências: pandas.

Amostrar por cliente, e não por pedido, preserva recompra e o histórico de cada
cliente; amostrar pedidos soltos destruiria a métrica de recompra.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from dq.settings import ROOT, get_settings

SAMPLE_DIR = ROOT / "data" / "sample"


def build_sample(fraction: float = 0.06, seed: int = 7) -> dict[str, int]:
    s = get_settings("default")
    src = s.path("source")
    t = {name: pd.read_parquet(src / f"{name}.parquet") for name in s.source["tables"]}
    rng = np.random.default_rng(seed)
    clientes = t["clientes"]
    keep = clientes[rng.random(len(clientes)) < fraction]
    ids = set(keep["cliente_id"])
    pedidos = t["pedidos"][t["pedidos"]["cliente_id"].isin(ids)]
    pids = set(pedidos["pedido_id"])
    out = {
        **{n: t[n] for n in ("regioes", "calendario_comercial", "categorias", "produtos", "variantes")},
        "clientes": keep,
        "enderecos": t["enderecos"][t["enderecos"]["cliente_id"].isin(ids)],
        "pedidos": pedidos,
        "itens_pedido": t["itens_pedido"][t["itens_pedido"]["pedido_id"].isin(pids)],
        "fretes": t["fretes"][t["fretes"]["pedido_id"].isin(pids)],
    }
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    counts = {}
    for name, df in out.items():
        df.reset_index(drop=True).to_parquet(SAMPLE_DIR / f"{name}.parquet", index=False, compression="zstd")
        counts[name] = len(df)
    return counts
