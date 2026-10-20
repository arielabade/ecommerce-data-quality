"""Acesso ao gabarito (só a avaliação pode usar).

Papel: carregar o registro de defeitos e a base gold, e expor os conjuntos
que a avaliação usa: quais pedidos/itens/dias têm cada tipo de defeito e
quais são outliers legítimos.
Entrada: ``<paths.defect_log>/defects.parquet`` e ``<paths.gold>``.
Saída: objeto ``Truth``.
Dependências: pandas.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

import pandas as pd

from dq.settings import Settings

ORDER_VALUE_ERRORS = ["frete_absurdo", "desconto_maior_que_subtotal", "frete_peso_incoerente"]
ITEM_VALUE_ERRORS = ["preco_zero", "preco_negativo", "preco_separador_perdido", "quantidade_negativa"]
LEGIT = ["legitimo_b2b", "legitimo_viral", "legitimo_black_friday"]
NOT_SALES = ["pedido_teste", "pedido_bot", "duplicata_aproximada"]


@dataclass
class Truth:
    log: pd.DataFrame
    gold: dict[str, pd.DataFrame]

    @classmethod
    def load(cls, s: Settings) -> Truth:
        log = pd.read_parquet(s.path("defect_log") / "defects.parquet")
        gold = {p.stem: pd.read_parquet(p) for p in s.path("gold").glob("*.parquet")}
        return cls(log, gold)

    def keys(self, tabela: str, tipos: list[str] | str) -> set[str]:
        tipos = [tipos] if isinstance(tipos, str) else tipos
        m = (self.log["tabela"] == tabela) & self.log["tipo_defeito"].isin(tipos)
        return set(self.log.loc[m, "chave"])

    def orders(self, tipos: list[str] | str) -> set[int]:
        tipos = [tipos] if isinstance(tipos, str) else tipos
        m = self.log["tipo_defeito"].isin(tipos) & self.log["pedido_ref"].notna()
        return set(self.log.loc[m, "pedido_ref"].astype(int))

    @cached_property
    def legit_orders(self) -> set[int]:
        return self.orders(LEGIT)

    def legit_by_type(self) -> dict[str, set[int]]:
        return {t: self.orders(t) for t in LEGIT}

    def days(self, tipos: list[str] | str) -> set[str]:
        tipos = [tipos] if isinstance(tipos, str) else tipos
        m = (self.log["tabela"] == "serie_diaria") & self.log["tipo_defeito"].isin(tipos)
        return set(self.log.loc[m, "chave"])
