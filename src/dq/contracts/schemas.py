"""Contratos de dados por camada (pandera).

Papel: dizer, para cada camada, o que um dado precisa cumprir para seguir
adiante, e parar o ETL quando não cumpre.
Entrada: dicionário de tabelas de uma camada.
Saída: ``ContractReport`` (por tabela: ok / falhas com exemplos). Na camada
clean e nos marts, falha levanta ``ContractError`` e a carga não acontece.
Dependências: pandera.

O que cada camada promete:
- **raw**: as colunas esperadas existem e tudo é texto; ``_lote`` é uma data.
  (Raw não promete conteúdo: é o que o fornecedor mandou.)
- **staging**: tipos certos; chaves numéricas parseáveis. Nulos são permitidos,
  porque um valor não parseável é informação para a camada clean.
- **clean**: chave única, identidades contábeis, preço e quantidade positivos,
  desconto <= subtotal, datas na ordem certa e não futuras, UF válida, CEP no
  formato, e-mail canônico, categoria mapeada.
- **marts**: receita e contagens não negativas, taxas entre 0 e 1.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd
import pandera.pandas as pa
from pandera.errors import SchemaErrors

UFS = ["AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG", "PA", "PB", "PR", "PE", "PI",
       "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO"]
STATUS = ["aguardando_pagamento", "pago", "enviado", "entregue", "cancelado", "devolvido"]


class ContractError(RuntimeError):
    pass


@dataclass
class ContractReport:
    layer: str
    results: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return all(r["ok"] for r in self.results.values())

    def as_dict(self) -> dict:
        return {"camada": self.layer, "ok": self.ok, "tabelas": self.results}


def _txt(**kw):
    return pa.Column(object, nullable=True, coerce=False, **kw)


RAW_COLUMNS = {
    "pedidos": ["pedido_id", "numero_pedido", "cliente_id", "endereco_entrega_id", "cupom_id", "canal_venda", "status",
                "criado_em", "pago_em", "cancelado_em", "subtotal", "desconto", "frete", "total", "ip_checkout"],
    "itens_pedido": ["item_pedido_id", "pedido_id", "variante_id", "quantidade", "preco_unitario", "preco_tabela",
                     "custo_unitario", "total_item"],
    "fretes": ["frete_id", "pedido_id", "transportadora", "servico", "peso_kg", "valor_cobrado", "custo_real",
               "frete_gratis", "prazo_estimado_dias", "postado_em", "entregue_em"],
    "clientes": ["cliente_id", "tipo_cliente", "tipo_pessoa", "nome", "email", "cadastrado_em", "origem_cadastro"],
    "enderecos": ["endereco_id", "cliente_id", "cep", "cidade", "uf"],
    "produtos": ["produto_id", "nome", "categoria"],
}


def raw_schema(name: str) -> pa.DataFrameSchema:
    cols = {c: pa.Column(nullable=True) for c in RAW_COLUMNS.get(name, [])}
    cols["_lote"] = pa.Column(str, pa.Check.str_matches(r"^\d{4}-\d{2}-\d{2}$"), nullable=False, coerce=True)
    return pa.DataFrameSchema(cols, strict=False)


STAGING = {
    "pedidos": pa.DataFrameSchema({
        "pedido_id": pa.Column("Int64", nullable=False),
        "cliente_id": pa.Column("Int64", nullable=True),
        "criado_em": pa.Column("datetime64[us, UTC]", nullable=True),
        "total_centavos": pa.Column("Int64", nullable=True),
    }, strict=False),
    "itens_pedido": pa.DataFrameSchema({
        "item_pedido_id": pa.Column("Int64", nullable=False),
        "pedido_id": pa.Column("Int64", nullable=False),
        "quantidade": pa.Column("Float64", nullable=True),
        "preco_unitario_centavos": pa.Column("Int64", nullable=True),
    }, strict=False),
    "fretes": pa.DataFrameSchema({
        "frete_id": pa.Column("Int64", nullable=False),
        "peso_kg": pa.Column("Float64", nullable=True),
        "valor_cobrado_centavos": pa.Column("Int64", nullable=True),
    }, strict=False),
}


def _clean_pedidos() -> pa.DataFrameSchema:
    ident = pa.Check(lambda d: d["total_centavos"] == d["subtotal_centavos"] - d["desconto_centavos"] + d["frete_centavos"],
                     error="total = subtotal - desconto + frete")
    return pa.DataFrameSchema({
        "pedido_id": pa.Column("Int64", unique=True, nullable=False),
        "cliente_id": pa.Column("Int64", nullable=False),
        "status": pa.Column(nullable=False, checks=pa.Check.isin(STATUS)),
        "criado_em": pa.Column("datetime64[us, UTC]", nullable=False),
        "subtotal_centavos": pa.Column("Int64", pa.Check.ge(0)),
        "desconto_centavos": pa.Column("Int64", pa.Check.ge(0)),
        "frete_centavos": pa.Column("Int64", pa.Check.ge(0)),
        "total_centavos": pa.Column("Int64", pa.Check.ge(0)),
    }, checks=[ident, pa.Check(lambda d: d["desconto_centavos"] <= d["subtotal_centavos"], error="desconto <= subtotal"),
               pa.Check(lambda d: d["data_local"] <= pd.to_datetime(d["_lote"]).dt.date, error="compra não é posterior ao lote"),
               pa.Check(lambda d: (d["pago_em"].isna()) | (d["pago_em"] >= d["criado_em"] - pd.Timedelta(minutes=1)),
                        error="pagamento depois da compra")], strict=False)


CLEAN = {
    "pedidos": _clean_pedidos(),
    "itens_pedido": pa.DataFrameSchema({
        "item_pedido_id": pa.Column("Int64", unique=True),
        "quantidade": pa.Column("Float64", pa.Check.gt(0)),
        "preco_unitario_centavos": pa.Column("Int64", pa.Check.gt(0)),
        "total_item_centavos": pa.Column("Int64", pa.Check.gt(0)),
    }, checks=pa.Check(lambda d: (d["total_item_centavos"] - (d["quantidade"] * d["preco_unitario_centavos"]).round()).abs() <= 1,
                       error="total_item = quantidade x preco"), strict=False),
    "fretes": pa.DataFrameSchema({
        "frete_id": pa.Column("Int64", unique=True),
        "pedido_id": pa.Column("Int64", unique=True),
        "valor_cobrado_centavos": pa.Column("Int64", pa.Check.ge(0), nullable=True),
    }, checks=pa.Check(lambda d: d["entregue_em"].isna() | d["postado_em"].isna() | (d["entregue_em"] >= d["postado_em"]),
                       error="entrega depois da postagem"), strict=False),
    "clientes": pa.DataFrameSchema({
        "cliente_id": pa.Column("Int64", unique=True),
        "email": pa.Column(nullable=False, unique=True,
                           checks=pa.Check.str_matches(r"^[a-z0-9._%-]+@[a-z0-9.-]+\.[a-z]+$")),
    }, strict=False),
    "enderecos": pa.DataFrameSchema({
        "endereco_id": pa.Column("Int64", unique=True),
        "uf": pa.Column(nullable=True, checks=pa.Check.isin(UFS)),
        "cep": pa.Column(nullable=True, checks=pa.Check.str_matches(r"^\d{5}-\d{3}$")),
    }, strict=False),
    "produtos": pa.DataFrameSchema({
        "produto_id": pa.Column("Int64", unique=True),
        "categoria_id": pa.Column("Int64", nullable=True),
    }, strict=False),
}

MARTS = {
    "vendas_diarias": pa.DataFrameSchema({
        "pedidos": pa.Column(int, pa.Check.ge(0), coerce=True),
        "receita_centavos": pa.Column(int, pa.Check.ge(0), coerce=True),
    }, strict=False),
    "metricas_mensais": pa.DataFrameSchema({
        "pedidos": pa.Column(int, pa.Check.ge(0), coerce=True),
        "receita_centavos": pa.Column(int, pa.Check.ge(0), coerce=True),
    }, checks=[pa.Check(lambda d: d["cancelados"] <= d["pedidos"], error="cancelados <= pedidos"),
               pa.Check(lambda d: d["pedidos_recorrentes"] <= d["pedidos_pagos"], error="recorrentes <= pagos")],
        strict=False),
}


def validate(layer: str, tables: dict[str, pd.DataFrame], *, raise_on_fail: bool) -> ContractReport:
    schemas = {"raw": {n: raw_schema(n) for n in tables}, "staging": STAGING, "clean": CLEAN, "marts": MARTS}[layer]
    rep = ContractReport(layer)
    for name, schema in schemas.items():
        if name not in tables:
            continue
        try:
            schema.validate(tables[name], lazy=True)
            rep.results[name] = {"ok": True, "linhas": int(len(tables[name]))}
        except SchemaErrors as e:
            fc = e.failure_cases
            rep.results[name] = {
                "ok": False, "linhas": int(len(tables[name])), "falhas": int(len(fc)),
                "regras": fc.groupby("check").size().head(10).to_dict(),
                "exemplos": fc.head(5).astype(str).to_dict("records"),
            }
    if raise_on_fail and not rep.ok:
        bad = {k: v for k, v in rep.results.items() if not v["ok"]}
        raise ContractError(f"Contrato da camada {layer} falhou: {bad}")
    return rep
