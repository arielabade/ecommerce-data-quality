"""Registro de tratamento: o que a limpeza fez, linha a linha.

Papel: cada regra de limpeza anota aqui o que detectou e o que decidiu
(remover, corrigir, limitar, imputar, sinalizar, quarentena), com o valor antes
e depois. É a trilha de auditoria do ETL e é o que ``dq.evaluate`` compara com
o registro de defeitos. Não confundir com o registro de defeitos: este é
produzido pelo ETL a partir dos dados; aquele é o gabarito, que o ETL nunca vê.
Entrada: chamadas ``add_many`` das regras.
Saída: DataFrame ``tabela, chave, coluna, regra, acao, valor_antes, valor_depois``.
Dependências: pandas.
"""

from __future__ import annotations

import pandas as pd

ACOES = ("removido", "corrigido", "limitado", "imputado", "anulado", "sinalizado", "quarentena", "padronizado")
COLUMNS = ["tabela", "chave", "coluna", "regra", "acao", "valor_antes", "valor_depois"]


def _str(s) -> list:
    if s is None:
        return None
    return [None if (v is None or (not isinstance(v, str) and pd.isna(v))) else str(v) for v in s]


class TreatmentLog:
    def __init__(self) -> None:
        self._parts: list[pd.DataFrame] = []

    def add_many(self, tabela: str, chaves, coluna: str, regra: str, acao: str, antes=None, depois=None) -> int:
        if acao not in ACOES:
            raise ValueError(f"ação desconhecida: {acao}")
        chaves = list(chaves)
        if not chaves:
            return 0
        n = len(chaves)
        df = pd.DataFrame({
            "tabela": tabela, "chave": _str(chaves), "coluna": coluna, "regra": regra, "acao": acao,
            "valor_antes": _str(antes) if antes is not None else [None] * n,
            "valor_depois": _str(depois) if depois is not None else [None] * n,
        })
        self._parts.append(df)
        return n

    def frame(self) -> pd.DataFrame:
        if not self._parts:
            return pd.DataFrame(columns=COLUMNS)
        return pd.concat(self._parts, ignore_index=True)[COLUMNS]

    def summary(self) -> pd.DataFrame:
        f = self.frame()
        return f.groupby(["tabela", "regra", "acao"]).size().rename("linhas").reset_index()
