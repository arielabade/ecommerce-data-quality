"""Perfil de dados por tabela.

Papel: descrever cada tabela antes de qualquer decisão de limpeza: tipos,
nulos, cardinalidade, distribuição, chaves, relacionamentos, faixas de valores
e padrões de texto. Serve para a origem (``docs/DATA_SOURCE.md``) e para o raw
(quanto da base chegou suja).
Entrada: dicionário ``{tabela: DataFrame}``.
Saída: dicionário serializável (JSON) com o perfil de cada coluna e o
resultado das checagens de chave e de relacionamento.
Dependências: pandas, numpy.

Padrão de texto: cada valor vira uma "forma" (dígito -> 9, letra minúscula -> a,
maiúscula -> A, acentuada -> á). ``"49000-000"`` vira ``99999-999``. As formas
mais comuns mostram em segundos se um campo tem mais de um formato.
"""

from __future__ import annotations

import re
import unicodedata

import numpy as np
import pandas as pd

PRIMARY_KEYS = {
    "regioes": ["uf"], "calendario_comercial": ["data"], "categorias": ["categoria_id"],
    "produtos": ["produto_id"], "variantes": ["variante_id"], "clientes": ["cliente_id"],
    "enderecos": ["endereco_id"], "pedidos": ["pedido_id"], "itens_pedido": ["item_pedido_id"],
    "fretes": ["frete_id"],
}

FOREIGN_KEYS = [
    ("categorias", "categoria_pai_id", "categorias", "categoria_id"),
    ("produtos", "categoria_id", "categorias", "categoria_id"),
    ("variantes", "produto_id", "produtos", "produto_id"),
    ("enderecos", "cliente_id", "clientes", "cliente_id"),
    ("enderecos", "uf", "regioes", "uf"),
    ("pedidos", "cliente_id", "clientes", "cliente_id"),
    ("pedidos", "endereco_entrega_id", "enderecos", "endereco_id"),
    ("itens_pedido", "pedido_id", "pedidos", "pedido_id"),
    ("itens_pedido", "variante_id", "variantes", "variante_id"),
    ("fretes", "pedido_id", "pedidos", "pedido_id"),
]


def shape_of(v: str) -> str:
    out = []
    for ch in str(v):
        if ch.isdigit():
            out.append("9")
        elif ch.isalpha():
            accented = len(unicodedata.normalize("NFKD", ch)) > 1
            out.append("á" if accented else ("A" if ch.isupper() else "a"))
        else:
            out.append(ch)
    s = "".join(out)
    s = re.sub(r"[aá]{3,}", "a+", s)
    return re.sub(r"A{3,}", "A+", s)


def _numeric_profile(s: pd.Series) -> dict:
    x = pd.to_numeric(s, errors="coerce").dropna().astype(float)
    if x.empty:
        return {}
    q = x.quantile([0, 0.01, 0.25, 0.5, 0.75, 0.99, 1]).to_list()
    iqr = q[4] - q[2]
    return {
        "min": q[0], "p01": q[1], "p25": q[2], "mediana": q[3], "p75": q[4], "p99": q[5], "max": q[6],
        "media": float(x.mean()), "desvio": float(x.std()), "assimetria": float(x.skew()),
        "fora_iqr_pct": float(((x < q[2] - 1.5 * iqr) | (x > q[4] + 1.5 * iqr)).mean() * 100),
        "zeros_pct": float((x == 0).mean() * 100), "negativos_pct": float((x < 0).mean() * 100),
    }


def profile_column(s: pd.Series) -> dict:
    n = len(s)
    nonnull = s.dropna()
    info = {
        "tipo": str(s.dtype),
        "nulos": int(n - len(nonnull)),
        "nulos_pct": round((n - len(nonnull)) / n * 100, 3) if n else 0.0,
        "distintos": int(nonnull.nunique()),
        "distintos_pct": round(nonnull.nunique() / max(len(nonnull), 1) * 100, 3),
    }
    if pd.api.types.is_bool_dtype(s):
        info["top"] = {str(k): int(v) for k, v in nonnull.value_counts().head(5).items()}
    elif pd.api.types.is_numeric_dtype(s):
        info.update(_numeric_profile(s))
        if info["distintos"] <= 12:
            info["top"] = {str(k): int(v) for k, v in nonnull.value_counts().head(12).items()}
    elif pd.api.types.is_datetime64_any_dtype(s):
        info.update({"min": str(nonnull.min()) if len(nonnull) else None,
                     "max": str(nonnull.max()) if len(nonnull) else None})
    else:
        txt = nonnull.astype(str)
        vc = txt.value_counts()
        info["top"] = {k: int(v) for k, v in vc.head(8).items()}
        lens = txt.str.len()
        info["comprimento"] = {"min": int(lens.min()) if len(lens) else 0, "max": int(lens.max()) if len(lens) else 0}
        sample = txt if len(txt) <= 200_000 else txt.sample(200_000, random_state=0)
        shapes = sample.map(shape_of).value_counts(normalize=True)
        info["formas"] = {k: round(float(v) * 100, 2) for k, v in shapes.head(6).items()}
        info["formas_distintas"] = int(len(shapes))
        info["espacos_nas_bordas_pct"] = round(float((txt != txt.str.strip()).mean() * 100), 3)
    return info


def profile_tables(tables: dict[str, pd.DataFrame]) -> dict:
    out: dict = {"tabelas": {}, "relacionamentos": []}
    for name, df in tables.items():
        cols = {c: profile_column(df[c]) for c in df.columns if not c.startswith("_")}
        pk = PRIMARY_KEYS.get(name)
        key = None
        if pk and all(c in df for c in pk):
            key = {"colunas": pk, "duplicadas": int(df.duplicated(pk).sum()),
                   "linhas_identicas": int(df.duplicated().sum())}
        out["tabelas"][name] = {"linhas": int(len(df)), "colunas": cols, "chave": key}
    for child, ccol, parent, pcol in FOREIGN_KEYS:
        if child in tables and parent in tables and ccol in tables[child] and pcol in tables[parent]:
            c = _keystr(tables[child][ccol].dropna())
            p = set(_keystr(tables[parent][pcol].dropna()))
            orphans = int((~c.isin(p)).sum())
            out["relacionamentos"].append({
                "filho": f"{child}.{ccol}", "pai": f"{parent}.{pcol}", "linhas_com_chave": int(len(c)),
                "orfas": orphans, "orfas_pct": round(orphans / max(len(c), 1) * 100, 3),
                "cardinalidade": _cardinality(tables[child][ccol]),
            })
    return out


def _keystr(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip().str.replace(r"\.0$", "", regex=True)


def _cardinality(s: pd.Series) -> str:
    vc = s.dropna().value_counts()
    if vc.empty:
        return "-"
    return "N:1 (único)" if vc.max() == 1 else f"N:1 (média {vc.mean():.2f} filhos por pai, máx {int(vc.max())})"


def to_jsonable(o):
    if isinstance(o, dict):
        return {str(k): to_jsonable(v) for k, v in o.items()}
    if isinstance(o, list):
        return [to_jsonable(v) for v in o]
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, (np.floating, float)):
        return None if np.isnan(o) else round(float(o), 4)
    return o
