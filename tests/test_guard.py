"""O código de limpeza nunca lê o registro de defeitos (nem a base gold).

Três camadas de proteção, três testes:
1. busca estática: nenhum módulo do caminho do ETL menciona o gabarito;
2. o bloqueio em tempo de execução levanta erro ao abrir um arquivo do gabarito;
3. o ETL inteiro roda com o bloqueio ativo, e um ETL "trapaceiro" é barrado.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import pytest

from dq.guard import TruthAccessError, truth_blocked
from dq.settings import ROOT

ETL_PATH = ["extract", "clean", "load", "contracts", "profile", "report/quality.py", "etl.py", "settings.py",
            "guard.py"]
FORBIDDEN = re.compile(r"defect_log|defects\.parquet|DefectLog|evaluate\.truth|\bTruth\b|paths?\(\s*[\"']gold")


def _etl_files() -> list[Path]:
    base = ROOT / "src" / "dq"
    files = []
    for part in ETL_PATH:
        p = base / part
        files += sorted(p.rglob("*.py")) if p.is_dir() else [p]
    return files


def test_etl_code_never_mentions_the_defect_log():
    offenders = []
    for f in _etl_files():
        text = f.read_text(encoding="utf-8")
        if f.name in ("settings.py", "guard.py"):  # só declaram/bloqueiam os caminhos
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if FORBIDDEN.search(line) and not line.lstrip().startswith(("#", '"', "'")):
                offenders.append(f"{f.relative_to(ROOT)}:{i}: {line.strip()}")
    assert not offenders, "código do ETL referencia o gabarito:\n" + "\n".join(offenders)


def test_etl_does_not_import_evaluate_or_corrupt():
    for f in _etl_files():
        text = f.read_text(encoding="utf-8")
        assert not re.search(r"from dq\.(evaluate|corrupt)|import dq\.(evaluate|corrupt)", text), f


def test_runtime_guard_blocks_reading_truth(tmp_path):
    log_dir = tmp_path / "defect_log"
    log_dir.mkdir()
    pd.DataFrame({"a": [1]}).to_parquet(log_dir / "defects.parquet")
    other = tmp_path / "ok.parquet"
    pd.DataFrame({"a": [1]}).to_parquet(other)
    with truth_blocked([log_dir]):
        pd.read_parquet(other)  # caminho permitido continua funcionando
        with pytest.raises(TruthAccessError):
            pd.read_parquet(log_dir / "defects.parquet")
        with pytest.raises(TruthAccessError):
            open(log_dir / "defects.parquet", "rb")
    pd.read_parquet(log_dir / "defects.parquet")  # fora do bloco: liberado (a avaliação precisa ler)


def test_etl_runs_with_guard_and_cheating_is_caught(corrupted, monkeypatch, tmp_path):
    from dq import etl
    from dq.clean import pipeline

    s = corrupted
    s.raw_config["paths"]["warehouse"] = str(tmp_path / "wh.duckdb")
    s.raw_config["paths"]["runs"] = str(tmp_path / "runs")
    s.raw_config["paths"]["reports"] = str(tmp_path / "reports")
    original = pipeline.run

    def cheating(raw, cfg):
        pd.read_parquet(s.path("defect_log") / "defects.parquet")  # "colar" do gabarito
        return original(raw, cfg)

    monkeypatch.setattr(pipeline, "run", cheating)
    with pytest.raises(TruthAccessError):
        etl.main(["all"], settings=s, verbose=False)
