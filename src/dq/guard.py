"""Bloqueio do gabarito durante o ETL.

Papel: garantir que a limpeza nunca "cole" do registro de defeitos nem da base
gold. Se colasse, precisão e recall medidos depois seriam fictícios.
Entrada: lista de diretórios proibidos (``Settings.truth_paths()``).
Saída: um context manager; dentro dele, qualquer ``open`` (Python, pandas,
pyarrow, duckdb via Python) de um arquivo sob esses diretórios levanta
``TruthAccessError``.
Dependências: ``sys.addaudithook`` (stdlib).

Por que um audit hook e não só uma busca no código: a busca estática pega
``read_parquet("data/defect_log")`` escrito à mão, mas não pega um caminho
montado em tempo de execução. O hook intercepta a abertura do arquivo em si.
Os dois existem: ``tests/test_guard.py`` faz a busca estática e roda o ETL
inteiro com o hook ativo.
"""

from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from pathlib import Path


class TruthAccessError(PermissionError):
    """O ETL tentou ler o gabarito (registro de defeitos ou base gold)."""


_forbidden: list[str] = []
_installed = False


def _hook(event: str, args: tuple) -> None:
    if not _forbidden or event not in ("open", "os.listdir", "os.scandir"):
        return
    target = args[0] if args else None
    if isinstance(target, int) or target is None:
        return
    try:
        resolved = os.path.realpath(os.fsdecode(target))
    except (TypeError, ValueError):
        return
    for root in _forbidden:
        if resolved == root or resolved.startswith(root + os.sep):
            raise TruthAccessError(f"ETL tentou abrir o gabarito: {resolved}")


@contextmanager
def truth_blocked(paths: list[Path]):
    """Ativa o bloqueio para os caminhos dados enquanto o bloco roda."""
    global _installed
    if not _installed:
        sys.addaudithook(_hook)  # hooks não podem ser removidos; ficam inertes fora do bloco
        _installed = True
    previous = list(_forbidden)
    _forbidden[:] = [os.path.realpath(p) for p in paths]
    try:
        yield
    finally:
        _forbidden[:] = previous
