"""Base de referência: a origem no esquema canônico, sem as imperfeições nativas.

Papel: transformar a extração da origem na forma "como deveria ser" que o
projeto usa como verdade. A origem já traz algumas imperfeições documentadas
em ``../ecommerce/docs/imperfeicoes.md`` que coincidem com defeitos que este
projeto injeta (e-mail com maiúsculas, CEP sem hífen, cidade sem acento,
pedidos de teste). Se ficassem, o registro de defeitos não seria a verdade
completa e a precisão medida sairia errada. Por isso são normalizadas aqui,
antes de qualquer injeção. Imperfeições nativas que este projeto não trata
(telefone, contas com mesmo CPF, nulos de cadastro) simplesmente não são
extraídas.
Entrada: tabelas de ``data/source``.
Saída: dicionário de DataFrames no esquema canônico (dinheiro em centavos
inteiros, datas em UTC com fuso, texto na grafia oficial).
Dependências: pandas, ``config/mappings/cidades_referencia.csv``.
"""

from __future__ import annotations

import unicodedata

import numpy as np
import pandas as pd

from dq.settings import CONFIG_DIR

TZ_LOCAL = "America/Sao_Paulo"


def unaccent(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def to_cents(x: pd.Series) -> pd.Series:
    return (pd.to_numeric(x).astype("float64") * 100).round().astype("Int64")


def local_to_utc(x: pd.Series) -> pd.Series:
    """Horário local da loja (sem fuso) -> UTC com fuso, truncado em segundos.

    O Brasil não tem horário de verão desde 2019, então a conversão é um
    deslocamento fixo de 3 h em todo o período; ainda assim a conversão usa a
    base de fusos, e não "+3h" escrito à mão.
    """
    ts = pd.to_datetime(x).dt.floor("s")
    return ts.dt.tz_localize(TZ_LOCAL).dt.tz_convert("UTC")


def build_baseline(src: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    out["regioes"] = src["regioes"].copy()
    cal = src["calendario_comercial"].copy()
    cal["data"] = pd.to_datetime(cal["data"]).dt.date
    out["calendario_comercial"] = cal
    out["categorias"] = src["categorias"].copy()

    cats = src["categorias"].set_index("categoria_id")["nome"]
    prod = src["produtos"].copy()
    prod["categoria"] = prod["categoria_id"].map(cats)
    prod["lancado_em"] = pd.to_datetime(prod["lancado_em"]).dt.date
    out["produtos"] = prod

    var = src["variantes"].copy()
    var["preco_atual_centavos"] = to_cents(var.pop("preco_atual"))
    var["custo_atual_centavos"] = to_cents(var.pop("custo_atual"))
    var["criado_em"] = pd.to_datetime(var["criado_em"]).dt.date
    out["variantes"] = var

    cli = src["clientes"].copy()
    cli["email"] = cli["email"].str.strip().str.lower()           # imperfeição nativa 18
    cli["cadastrado_em"] = local_to_utc(cli["cadastrado_em"])
    out["clientes"] = cli

    ref = pd.read_csv(CONFIG_DIR / "mappings" / "cidades_referencia.csv")
    ref["k"] = ref["cidade"].map(lambda c: unaccent(c).lower())
    canon = {(r.uf, r.k): r.cidade for r in ref.itertuples()}
    end = src["enderecos"].copy()
    k = end["cidade"].map(lambda c: unaccent(str(c)).lower().strip())
    end["cidade"] = [canon.get((u, kk), c) for u, kk, c in zip(end["uf"], k, end["cidade"], strict=True)]  # nativa 20
    digits = end["cep"].str.replace(r"\D", "", regex=True).str.zfill(8)
    end["cep"] = digits.str[:5] + "-" + digits.str[5:]                                                       # nativa 20
    end["criado_em"] = local_to_utc(end["criado_em"])
    out["enderecos"] = end

    ped = src["pedidos"].copy()
    teste = ped.loc[ped["pedido_teste"] == 1, "pedido_id"]                                                   # nativa 16
    ped = ped[ped["pedido_teste"] == 0].drop(columns="pedido_teste")
    for c in ("criado_em", "pago_em", "cancelado_em"):
        ped[c] = local_to_utc(ped[c])
    for c in ("subtotal", "desconto", "frete", "total"):
        ped[f"{c}_centavos"] = to_cents(ped.pop(c))
    for c in ("endereco_entrega_id", "cupom_id"):
        ped[c] = ped[c].astype("Int64")
    ped["numero_pedido"] = ped["numero_pedido"].astype(str)
    ped["ip_checkout"] = _checkout_ips(ped)
    out["pedidos"] = ped.reset_index(drop=True)

    itens = src["itens_pedido"].copy()
    itens = itens[~itens["pedido_id"].isin(teste)]
    for c in ("preco_unitario", "preco_tabela", "custo_unitario", "total_item"):
        itens[f"{c}_centavos"] = to_cents(itens.pop(c))
    itens["quantidade"] = itens["quantidade"].astype("float64").round(2)
    out["itens_pedido"] = itens.reset_index(drop=True)

    fr = src["fretes"].copy()
    fr = fr[~fr["pedido_id"].isin(teste)]
    for c in ("valor_cobrado", "custo_real"):
        fr[f"{c}_centavos"] = to_cents(fr.pop(c))
    for c in ("postado_em", "entregue_em"):
        fr[c] = local_to_utc(fr[c])
    fr["peso_kg"] = fr["peso_kg"].astype("float64").round(3)
    out["fretes"] = fr.reset_index(drop=True)
    return out


def _checkout_ips(ped: pd.DataFrame) -> list[str]:
    """IP do checkout: a origem não registra; aqui ele é sintético e determinístico.

    Cada cliente tem um IP "de casa" (80% dos pedidos) e o resto sai de uma
    rede móvel qualquer. É o que permite detectar bots por IP depois.
    """
    rng = np.random.default_rng(424242)
    prefixes = np.array([177, 179, 186, 187, 189, 191, 200, 201])
    ids = ped["cliente_id"].to_numpy()
    uniq = np.unique(ids)
    home = {int(c): f"{prefixes[c % 8]}.{(c * 7) % 250 + 1}.{(c * 13) % 250 + 1}.{(c * 31) % 250 + 1}" for c in uniq}
    mobile = rng.random(len(ped)) < 0.2
    rand = rng.integers(1, 255, size=(len(ped), 3))
    pre = rng.choice(prefixes, size=len(ped))
    return [
        f"{pre[i]}.{rand[i, 0]}.{rand[i, 1]}.{rand[i, 2]}" if mobile[i] else home[int(ids[i])]
        for i in range(len(ped))
    ]
