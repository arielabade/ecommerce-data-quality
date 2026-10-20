"""Fixtures: um perfil pequeno isolado em diretório temporário."""

from __future__ import annotations

import copy

import pytest

from dq.settings import ROOT, Settings, get_settings


def make_settings(tmp_path) -> Settings:
    base = get_settings("small")
    cfg = copy.deepcopy(base.raw_config)
    cfg["source"]["kind"] = "parquet"
    cfg["source"]["parquet_dir"] = str(ROOT / "data" / "sample")
    for k in ("source", "gold", "raw", "defect_log", "runs", "reports", "dashboard", "figures"):
        cfg["paths"][k] = str(tmp_path / k)
    cfg["paths"]["warehouse"] = str(tmp_path / "warehouse" / "dq.duckdb")
    return Settings(profile="small", raw_config=cfg, corruption=copy.deepcopy(base.corruption),
                    cleaning=copy.deepcopy(base.cleaning))


@pytest.fixture
def small_settings(tmp_path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture(scope="session")
def corrupted(tmp_path_factory):
    """Perfil pequeno extraído e corrompido uma vez por sessão (somente leitura nos testes)."""
    from dq.corrupt.__main__ import run as corrupt
    from dq.extract.source import extract

    s = make_settings(tmp_path_factory.mktemp("small"))
    extract(s)
    corrupt(s)
    return s
