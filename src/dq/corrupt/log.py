"""Registro de defeitos (o gabarito da limpeza).

Papel: acumular uma linha por defeito injetado (ou por outlier legítimo
marcado) e gravar em ``data/defect_log/defects.parquet``.
Entrada: chamadas de ``DefectLog.add`` feitas pelo injetor.
Saída: tabela com ``tabela, chave, coluna, tipo_defeito, valor_original,
valor_sujo, legitimo, pedido_ref, detalhe``.
Dependências: pandas.

Quem pode ler este arquivo: ``dq.evaluate`` (para medir a limpeza) e
``dq.report``. Quem não pode: tudo que roda dentro do ETL. ``dq.guard`` bloqueia
a leitura em tempo de execução e ``tests/test_guard.py`` falha se alguém tentar.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

COLUMNS = ["defeito_id", "tabela", "chave", "coluna", "tipo_defeito", "valor_original", "valor_sujo",
           "legitimo", "pedido_ref", "detalhe"]


def _s(v) -> str | None:
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return str(v)


class DefectLog:
    def __init__(self) -> None:
        self._rows: list[tuple] = []

    def add(self, tabela: str, chave, coluna: str, tipo: str, original=None, sujo=None, *,
            legitimo: bool = False, pedido_ref=None, detalhe: str | None = None) -> None:
        self._rows.append((tabela, _s(chave), coluna, tipo, _s(original), _s(sujo), legitimo, _s(pedido_ref), detalhe))

    def add_many(self, tabela: str, chaves, coluna: str, tipo: str, originais=None, sujos=None, *,
                 legitimo: bool = False, pedido_refs=None, detalhe: str | None = None, detalhes=None) -> None:
        chaves = list(chaves)
        n = len(chaves)
        originais = list(originais) if originais is not None else [None] * n
        sujos = list(sujos) if sujos is not None else [None] * n
        refs = list(pedido_refs) if pedido_refs is not None else [None] * n
        dets = list(detalhes) if detalhes is not None else [detalhe] * n
        for k, o, s, r, d in zip(chaves, originais, sujos, refs, dets, strict=True):
            self.add(tabela, k, coluna, tipo, o, s, legitimo=legitimo, pedido_ref=r, detalhe=d)

    def frame(self) -> pd.DataFrame:
        df = pd.DataFrame(self._rows, columns=COLUMNS[1:])
        df.insert(0, "defeito_id", range(1, len(df) + 1))
        return df

    def write(self, directory: Path) -> pd.DataFrame:
        directory.mkdir(parents=True, exist_ok=True)
        df = self.frame()
        df.to_parquet(directory / "defects.parquet", index=False)
        summary = (df.groupby(["tabela", "tipo_defeito", "legitimo"]).size().rename("linhas").reset_index())
        summary.to_csv(directory / "resumo.csv", index=False)
        return df
