"""Perfil -> Markdown (``docs/DATA_SOURCE.md``).

Papel: transformar o JSON do perfil num documento legível, com um resumo
por tabela e as checagens de chave e relacionamento.
Entrada: saída de ``profiler.profile_tables`` + fatos de negócio calculados em
``facts`` (sazonalidade, identidades contábeis).
Saída: texto Markdown.
Dependências: nenhuma além da stdlib.
"""

from __future__ import annotations

INTRO = """# Fonte de dados: Tecelã Tecidos

> Gerado por `make data` (`python -m dq.profile`) a partir da extração da origem.
> Os números abaixo são da escala 1.0 do gerador. **Dados simulados.**

## O que é a origem

A Tecelã Tecidos é uma loja online brasileira de tecidos, **fictícia**, cujo banco
MySQL é gerado pelo repositório [`../ecommerce`](https://github.com/arielabade/dados-sinteticos-ecommerce-tecidos)
(`schema.sql`, `gerador/`, `dbt/`). Ele cobre 01/10/2023 a 13/10/2026 e tem 45 tabelas
(transacional, tracking GA4 server-side, mídia paga, MMM). Este projeto lê **10 delas**:
as que formam pedidos, clientes, catálogo e geografia. Tracking e mídia (mais de 20
milhões de linhas) ficam de fora porque nenhuma métrica aqui depende deles.

| Camada | Tabelas lidas | Grão |
| --- | --- | --- |
| Geografia e calendário | `regioes`, `calendario_comercial` | UF; dia |
| Catálogo | `categorias`, `produtos`, `variantes` | categoria (2 níveis); tecido; SKU (cor x estampa x metro/rolo) |
| Cadastro | `clientes`, `enderecos` | cliente; endereço |
| Transação | `pedidos`, `itens_pedido`, `fretes` | pedido; item; envio (1 por pedido) |

### Convenções da origem que importam para a limpeza

- **Fuso:** tabelas transacionais em horário local (`America/Sao_Paulo`, sem fuso
  gravado); o Brasil não tem horário de verão desde 2019, então local = UTC-3 em
  todo o período. Este projeto guarda tudo em UTC e apresenta em São Paulo.
- **Dinheiro:** `DECIMAL(12,2)` em reais. Aqui vira centavos inteiros.
- **Receita paga:** pedidos com status `pago`, `enviado`, `entregue` ou `devolvido`
  (mesma regra do `stg_pedidos` do dbt da origem).
- **Identidades que valem em 100% das linhas da origem** (e por isso viram regras):
{identities}

### Imperfeições que a origem já tem

A origem documenta 24 imperfeições propositais (`../ecommerce/docs/imperfeicoes.md`).
Quatro coincidem com defeitos que este projeto injeta e são **normalizadas antes da
injeção** (`src/dq/corrupt/baseline.py`), para que o registro de defeitos seja a
verdade completa: e-mail com maiúsculas/espaço (~1,5%), CEP sem hífen (~12%), cidade
sem acento ou em maiúsculas (~6%) e 12 pedidos de teste (`pedido_teste = 1`). As
demais (telefone, contas com mesmo CPF, nulos de cadastro, tracking) não entram
porque as colunas não são extraídas.

## Achados do perfil

{findings}

## Relacionamentos

| Filho | Pai | Linhas com chave | Órfãs | Cardinalidade |
| --- | --- | ---: | ---: | --- |
{relationships}

## Perfil por tabela
"""


def _fmt(v, nd=2):
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:,.{nd}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    if isinstance(v, int):
        return f"{v:,}".replace(",", ".")
    return str(v)


def _col_row(name: str, c: dict) -> str:
    if "mediana" in c:
        dist = f"{_fmt(c['min'])} · {_fmt(c['mediana'])} · {_fmt(c['p99'])} · {_fmt(c['max'])}"
        extra = f"assimetria {_fmt(c['assimetria'])}; fora do IQR {_fmt(c['fora_iqr_pct'])}%"
    elif "formas" in c:
        dist = ", ".join(f"`{k}` {_fmt(v, 1)}%" for k, v in list(c["formas"].items())[:3])
        top = ", ".join(f"{k} ({_fmt(v)})" for k, v in list(c["top"].items())[:3])
        extra = f"top: {top}" if c["distintos"] <= 50 else f"{c['formas_distintas']} formas"
    elif "min" in c:
        dist = f"{str(c['min'])[:19]} → {str(c['max'])[:19]}"
        extra = ""
    else:
        dist, extra = "", ""
    return (f"| `{name}` | {c['tipo']} | {_fmt(c['nulos_pct'], 1)}% | {_fmt(c['distintos'])} | "
            f"{dist} | {extra} |")


def render(profile: dict, facts: dict) -> str:
    rel = "\n".join(
        f"| `{r['filho']}` | `{r['pai']}` | {_fmt(r['linhas_com_chave'])} | {_fmt(r['orfas'])} | {r['cardinalidade']} |"
        for r in profile["relacionamentos"]
    )
    out = [INTRO.format(identities="\n".join(f"  - {x}" for x in facts["identidades"]),
                        findings="\n".join(f"- {x}" for x in facts["achados"]), relationships=rel)]
    for name, t in profile["tabelas"].items():
        key = t["chave"]
        k = (f"chave `{', '.join(key['colunas'])}`: {key['duplicadas']} duplicadas, "
             f"{key['linhas_identicas']} linhas idênticas" if key else "sem chave declarada")
        out.append(f"\n### `{name}` — {_fmt(t['linhas'])} linhas ({k})\n")
        out.append("| Coluna | Tipo | Nulos | Distintos | Distribuição (mín · mediana · p99 · máx) ou formas | Notas |")
        out.append("| --- | --- | ---: | ---: | --- | --- |")
        out += [_col_row(c, info) for c, info in t["colunas"].items()]
    return "\n".join(out) + "\n"
