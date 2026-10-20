"""Extração fiel do banco de origem.

Papel: copiar as tabelas transacionais e de cadastro do MySQL da Tecelã Tecidos
(repositório ``../ecommerce``, só leitura) para Parquet, sem mudar nenhum valor.
Entrada: MySQL de origem (URL montada de variáveis de ambiente) ou, no perfil
pequeno, a amostra versionada em ``data/sample``.
Saída: ``<paths.source>/<tabela>.parquet``, uma por tabela de ``source.tables``.
Dependências: SQLAlchemy + PyMySQL (só no modo mysql), pandas, pyarrow.

Só as 10 tabelas que formam pedidos, clientes, catálogo e geografia são
extraídas. Tracking e mídia (mais de 20 milhões de linhas) ficam de fora porque
nenhuma métrica deste projeto depende deles.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import pandas as pd

from dq.settings import ROOT, Settings, get_settings

# Colunas por tabela: as que entram no projeto. Escolher explicitamente evita
# trazer PII que nenhuma análise usa (CPF, telefone, logradouro).
COLUMNS: dict[str, list[str]] = {
    "regioes": ["uf", "nome", "macrorregiao", "sigla_macrorregiao"],
    "calendario_comercial": ["data", "dia_semana", "feriado_nacional", "nome_feriado", "evento_comercial",
                             "promocao_loja", "nome_promocao"],
    "categorias": ["categoria_id", "categoria_pai_id", "nome", "slug"],
    "produtos": ["produto_id", "sku_base", "nome", "categoria_id", "fornecedor_id", "composicao", "largura_cm",
                 "lancado_em", "ativo"],
    "variantes": ["variante_id", "produto_id", "sku", "cor", "estampa", "formato_venda", "metros_por_unidade",
                  "preco_atual", "custo_atual", "ativo", "criado_em"],
    "clientes": ["cliente_id", "tipo_cliente", "tipo_pessoa", "nome", "razao_social", "email", "cadastrado_em",
                 "origem_cadastro"],
    "enderecos": ["endereco_id", "cliente_id", "tipo", "cep", "bairro", "cidade", "uf", "principal", "criado_em"],
    "pedidos": ["pedido_id", "numero_pedido", "cliente_id", "endereco_entrega_id", "cupom_id", "canal_venda",
                "status", "criado_em", "pago_em", "cancelado_em", "subtotal", "desconto", "frete", "total",
                "pedido_teste"],
    "itens_pedido": ["item_pedido_id", "pedido_id", "variante_id", "quantidade", "preco_unitario", "preco_tabela",
                     "custo_unitario", "total_item"],
    "fretes": ["frete_id", "pedido_id", "transportadora", "servico", "peso_kg", "valor_cobrado", "custo_real",
               "frete_gratis", "prazo_estimado_dias", "postado_em", "entregue_em"],
}

DECIMALS = {
    "variantes": ["metros_por_unidade", "preco_atual", "custo_atual"],
    "pedidos": ["subtotal", "desconto", "frete", "total"],
    "itens_pedido": ["quantidade", "preco_unitario", "preco_tabela", "custo_unitario", "total_item"],
    "fretes": ["peso_kg", "valor_cobrado", "custo_real"],
}


def _from_mysql(settings: Settings, table: str) -> pd.DataFrame:
    from sqlalchemy import create_engine

    engine = create_engine(settings.source_url())
    cols = ", ".join(f"`{c}`" for c in COLUMNS[table])
    with engine.connect() as conn:
        df = pd.read_sql(f"SELECT {cols} FROM `{table}`", conn)  # noqa: S608 - nomes vêm de COLUMNS
    for c in DECIMALS.get(table, []):
        df[c] = pd.to_numeric(df[c]).astype("float64")  # DECIMAL chega como objeto Decimal
    return df


def _from_csv(settings: Settings, table: str) -> pd.DataFrame:
    """CSVs do gerador da origem (o mesmo conteúdo que o MySQL carrega); ``\\N`` = nulo."""
    d = Path(settings.source.get("csv_dir", "../ecommerce/data/csv"))
    d = d if d.is_absolute() else ROOT / d
    df = pd.read_csv(d / f"{table}.csv", usecols=COLUMNS[table], na_values=["\\N"], keep_default_na=False,
                     low_memory=False)
    return df[COLUMNS[table]]


def extract(settings: Settings | None = None) -> dict[str, int]:
    """Copia cada tabela da origem para Parquet e devolve as contagens de linhas."""
    s = settings or get_settings()
    out = s.path("source")
    out.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    kind = s.source["kind"]
    for table in s.source["tables"]:
        t0 = time.time()
        if kind in ("mysql", "csv"):
            df = _from_mysql(s, table) if kind == "mysql" else _from_csv(s, table)
            df.to_parquet(out / f"{table}.parquet", index=False)
        elif kind == "parquet":
            src = _parquet_dir(s) / f"{table}.parquet"
            if src.resolve() != (out / f"{table}.parquet").resolve():
                shutil.copyfile(src, out / f"{table}.parquet")
            df = pd.read_parquet(out / f"{table}.parquet")
        else:
            raise ValueError(f"source.kind desconhecido: {kind}")
        counts[table] = len(df)
        print(f"  {table:<22} {len(df):>9,} linhas  ({time.time() - t0:.1f}s)")
    return counts


def _parquet_dir(s: Settings) -> Path:
    p = Path(s.source["parquet_dir"])
    return p if p.is_absolute() else ROOT / p


def load_source(settings: Settings | None = None) -> dict[str, pd.DataFrame]:
    s = settings or get_settings()
    return {t: pd.read_parquet(s.path("source") / f"{t}.parquet") for t in s.source["tables"]}
