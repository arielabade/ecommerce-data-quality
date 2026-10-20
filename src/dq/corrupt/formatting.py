"""Serialização para o raw: tudo vira texto, como num export de CSV.

Papel: converter as tabelas tipadas (já com defeitos de valor) para texto,
aplicando variações de formato que um export real tem: moeda em formato
brasileiro, datas dd/mm/aaaa e datas ISO em UTC explícito.
Entrada: tabelas tipadas do ``Injector`` e ``corruption.yaml: formatos``.
Saída: DataFrames com todas as colunas ``string`` + ``_lote``.
Dependências: numpy, pandas.

A escolha do formato é por chave, não por linha: assim uma duplicata exata
continua idêntica byte a byte, como acontece num reenvio real.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from dq.corrupt.baseline import TZ_LOCAL
from dq.corrupt.log import DefectLog

MONEY = {
    "pedidos": ["subtotal", "desconto", "frete", "total"],
    "itens_pedido": ["preco_unitario", "preco_tabela", "custo_unitario", "total_item"],
    "fretes": ["valor_cobrado", "custo_real"],
    "variantes": ["preco_atual", "custo_atual"],
}
DATES = {
    "pedidos": ["criado_em", "pago_em", "cancelado_em"],
    "fretes": ["postado_em", "entregue_em"],
    "clientes": ["cadastrado_em"],
    "enderecos": ["criado_em"],
}
KEY = {"pedidos": "pedido_id", "itens_pedido": "item_pedido_id", "fretes": "frete_id", "clientes": "cliente_id",
       "enderecos": "endereco_id", "produtos": "produto_id", "variantes": "variante_id"}


def _money(cents: pd.Series, br: np.ndarray) -> pd.Series:
    out = []
    for c, b in zip(cents.tolist(), br, strict=True):
        if c is None or c is pd.NA or (isinstance(c, float) and np.isnan(c)):
            out.append(None)
            continue
        c = int(c)
        sign = "-" if c < 0 else ""
        r, cc = divmod(abs(c), 100)
        out.append(f"{sign}{r:,}".replace(",", ".") + f",{cc:02d}" if b else f"{sign}{r}.{cc:02d}")
    return pd.Series(out, index=cents.index, dtype="string")


def _num(x: pd.Series, decimals: int, br: np.ndarray) -> pd.Series:
    s = x.map(lambda v: None if pd.isna(v) else f"{float(v):.{decimals}f}")
    s = s.where(~(br & s.notna()), s.str.replace(".", ",", regex=False))
    return s.astype("string")


def _dates(ts: pd.Series, fmt: np.ndarray) -> pd.Series:
    ts = pd.to_datetime(ts, utc=True)  # linhas novas (concat de dicts) podem chegar como object
    local = ts.dt.tz_convert(TZ_LOCAL)
    std = local.dt.strftime("%Y-%m-%d %H:%M:%S")
    br = local.dt.strftime("%d/%m/%Y %H:%M:%S")
    iso = ts.dt.tz_convert("UTC").dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    out = np.where(fmt == 1, br, np.where(fmt == 2, iso, std))
    return pd.Series(out, index=ts.index, dtype="string").where(ts.notna(), None)


def to_raw(t: dict[str, pd.DataFrame], cfg: dict, log: DefectLog, no_iso: set[int]) -> dict[str, pd.DataFrame]:
    f = cfg["formatos"]
    rng = np.random.default_rng(int(cfg["seed"]) + 1)
    raw: dict[str, pd.DataFrame] = {}
    for name, df in t.items():
        df = df.copy()
        key = KEY.get(name)
        keys = df[key].astype("int64").to_numpy() if key else np.arange(len(df))
        size = int(keys.max()) + 1 if len(keys) else 1
        draw_money = rng.random(size)
        draw_date = rng.random(size)
        br_money = draw_money[keys] < f["moeda_br"] if name in MONEY else np.zeros(len(df), bool)
        date_fmt = np.zeros(len(df), int)
        if name in ("pedidos", "fretes"):
            d = draw_date[keys]
            date_fmt = np.where(d < f["data_br"], 1, np.where(d < f["data_br"] + f["data_iso_utc"], 2, 0))
            if name == "pedidos":
                date_fmt[np.isin(keys, list(no_iso)) & (date_fmt == 2)] = 0
        for c in MONEY.get(name, []):
            df[c] = _money(df.pop(f"{c}_centavos"), br_money)
        for c in DATES.get(name, []):
            df[c] = _dates(df[c], date_fmt)
        if "quantidade" in df:
            df["quantidade"] = _num(df["quantidade"], 2, br_money)
        if "peso_kg" in df:
            df["peso_kg"] = _num(df["peso_kg"], 3, br_money)
        if "metros_por_unidade" in df:
            df["metros_por_unidade"] = _num(df["metros_por_unidade"], 2, br_money)
        if name == "produtos":
            df = df.drop(columns=["categoria_id"])
        for c in df.columns:
            if c == "_lote":
                continue
            col = df[c]
            if pd.api.types.is_datetime64_any_dtype(col):
                col = col.dt.strftime("%Y-%m-%d")
            elif col.dtype == object and len(col) and hasattr(col.dropna().iloc[0] if col.notna().any() else None, "isoformat"):
                col = col.map(lambda v: None if pd.isna(v) else v.isoformat())
            elif pd.api.types.is_float_dtype(col):
                col = col.map(lambda v: None if pd.isna(v) else (str(int(v)) if float(v).is_integer() else str(v)))
            df[c] = col.astype("string")
        if key and name in ("pedidos", "itens_pedido", "fretes"):
            uniq = ~pd.Series(keys).duplicated().to_numpy()
            k_br = keys[br_money & uniq]
            log.add_many(name, k_br, "*moeda", "formato_moeda_br", pedido_refs=df.loc[br_money & uniq, "pedido_id"])
            if name in ("pedidos", "fretes"):
                for code, tipo in ((1, "formato_data_br"), (2, "formato_data_iso_utc")):
                    m = (date_fmt == code) & uniq
                    log.add_many(name, keys[m], "*data", tipo, pedido_refs=df.loc[m, "pedido_id"])
        raw[name] = df.reset_index(drop=True)
    return raw
