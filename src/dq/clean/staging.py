"""Staging: do texto do raw para tipos, sem decidir nada sobre o conteúdo.

Papel: converter cada coluna do raw para o tipo certo, entendendo os formatos
que um export brasileiro produz. Nenhuma linha é removida e nenhum valor é
"corrigido" aqui; isso é trabalho da camada clean. O que sai daqui é o que um
analista teria depois de carregar e tipar o CSV com cuidado: é a base do
"antes da limpeza" nas comparações de impacto.
Entrada: tabelas do raw (todas as colunas texto + ``_lote``).
Saída: tabelas tipadas: dinheiro em centavos ``Int64``, datas em UTC com fuso,
ids ``Int64``, texto só aparado.
Dependências: pandas, numpy.

Por que centavos inteiros: soma de float acumula erro de arredondamento
(0,1 + 0,2 != 0,3) e a reconciliação ``total = subtotal - desconto + frete``
precisa ser exata.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TZ_LOCAL = "America/Sao_Paulo"

INT_COLS = {
    "pedidos": ["pedido_id", "cliente_id", "endereco_entrega_id", "cupom_id"],
    "itens_pedido": ["item_pedido_id", "pedido_id", "variante_id"],
    "fretes": ["frete_id", "pedido_id", "frete_gratis", "prazo_estimado_dias"],
    "clientes": ["cliente_id"],
    "enderecos": ["endereco_id", "cliente_id", "principal"],
    "produtos": ["produto_id", "fornecedor_id", "largura_cm", "ativo"],
    "variantes": ["variante_id", "produto_id", "ativo"],
    "categorias": ["categoria_id", "categoria_pai_id"],
    "calendario_comercial": ["dia_semana", "feriado_nacional", "promocao_loja"],
}
MONEY_COLS = {
    "pedidos": ["subtotal", "desconto", "frete", "total"],
    "itens_pedido": ["preco_unitario", "preco_tabela", "custo_unitario", "total_item"],
    "fretes": ["valor_cobrado", "custo_real"],
    "variantes": ["preco_atual", "custo_atual"],
}
DECIMAL_COLS = {"itens_pedido": ["quantidade"], "fretes": ["peso_kg"], "variantes": ["metros_por_unidade"]}
DATETIME_COLS = {
    "pedidos": ["criado_em", "pago_em", "cancelado_em"],
    "fretes": ["postado_em", "entregue_em"],
    "clientes": ["cadastrado_em"],
    "enderecos": ["criado_em"],
}
DATE_COLS = {"produtos": ["lancado_em"], "variantes": ["criado_em"], "calendario_comercial": ["data"]}


def parse_decimal(s: pd.Series) -> pd.Series:
    """Número em formato brasileiro ou americano -> float.

    Regra: se tem vírgula, a vírgula é o decimal e o ponto é milhar
    ("1.234,56"); se não tem, o ponto é o decimal ("1234.56"). Um export não
    mistura os dois papéis do ponto na mesma célula, então a regra é segura.
    """
    t = s.astype("string").str.strip()
    br = t.str.contains(",", regex=False, na=False)
    t = t.where(~br, t.str.replace(".", "", regex=False).str.replace(",", ".", regex=False))
    return pd.to_numeric(t, errors="coerce").astype("Float64")


def parse_money(s: pd.Series) -> pd.Series:
    """Reais em texto -> centavos inteiros."""
    return (parse_decimal(s) * 100).round().astype("Int64")


def parse_datetime(s: pd.Series, local_tz: str = TZ_LOCAL) -> pd.Series:
    """Texto de data/hora em três formatos -> UTC com fuso.

    - ``2024-05-03 13:22:01``: horário local da loja (padrão do export);
    - ``03/05/2024 13:22:01``: horário local, formato brasileiro;
    - ``2024-05-03T16:22:01Z`` ou com ``+00:00``/``-03:00``: fuso explícito.
    Tratar a terceira forma como local seria errar 3 horas sem nenhum aviso.
    """
    t = s.astype("string").str.strip()
    out = pd.Series(pd.NaT, index=s.index, dtype="datetime64[us, UTC]")
    explicit = t.str.contains(r"(?:Z|[+-]\d{2}:?\d{2})$", regex=True, na=False)
    br = t.str.match(r"^\d{2}/\d{2}/\d{4}", na=False)
    iso_local = t.notna() & ~explicit & ~br
    if explicit.any():
        out[explicit] = pd.to_datetime(t[explicit], utc=True, format="ISO8601", errors="coerce")
    for mask, fmt in ((br, "%d/%m/%Y %H:%M:%S"), (iso_local, "%Y-%m-%d %H:%M:%S")):
        if mask.any():
            naive = pd.to_datetime(t[mask], format=fmt, errors="coerce")
            out[mask] = naive.dt.tz_localize(local_tz, nonexistent="shift_forward", ambiguous="NaT").dt.tz_convert("UTC")
    return out


def stage_table(name: str, raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.copy()
    for c in df.columns:
        if df[c].dtype == object or str(df[c].dtype) in ("string", "str"):
            df[c] = df[c].astype("string").str.strip().replace("", pd.NA)
    for c in INT_COLS.get(name, []):
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce").astype("Int64")
    for c in MONEY_COLS.get(name, []):
        if c in df:
            df[f"{c}_centavos"] = parse_money(df.pop(c))
    for c in DECIMAL_COLS.get(name, []):
        if c in df:
            df[c] = parse_decimal(df[c])
    for c in DATETIME_COLS.get(name, []):
        if c in df:
            df[c] = parse_datetime(df[c])
    for c in DATE_COLS.get(name, []):
        if c in df:
            df[c] = pd.to_datetime(df[c], errors="coerce").dt.date
    if "_lote" in df:
        df["_lote"] = pd.to_datetime(df["_lote"]).dt.date
    return df


def stage_all(raw: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    return {name: stage_table(name, df) for name, df in raw.items()}


def local_date(ts: pd.Series) -> pd.Series:
    """Data de negócio: o dia em São Paulo (é o dia que o time comercial enxerga)."""
    return ts.dt.tz_convert(TZ_LOCAL).dt.date


def row_hash(df: pd.DataFrame, cols: list[str]) -> pd.Series:
    """Hash estável do conteúdo de negócio de uma linha (metadados de lote fora)."""
    return pd.util.hash_pandas_object(df[cols].astype("string").fillna("\x00"), index=False).astype(np.uint64)
