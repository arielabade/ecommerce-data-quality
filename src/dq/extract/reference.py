"""Tabela de referência de cidades, UFs e faixas de CEP.

Papel: produzir ``config/mappings/cidades_referencia.csv``, a lista oficial de
grafias que a padronização de endereço usa como alvo. Num cliente real esse
papel é da tabela do IBGE e da faixa de CEP dos Correios; aqui ela sai da
mesma lista que o gerador da origem usa, lida como texto (sem importar o
gerador, que exige Faker).
Entrada: ``../ecommerce/gerador/clientes.py`` (só leitura).
Saída: CSV versionado com ``uf, cidade, cep5_min, cep5_max``.
Dependências: ast (stdlib), pandas.

Roda uma vez e o resultado vai para o git: a limpeza depende do arquivo
versionado, não do repositório de origem.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pandas as pd

from dq.settings import CONFIG_DIR, ROOT


def build_city_reference(ecommerce_dir: Path | None = None) -> pd.DataFrame:
    src_dir = ecommerce_dir or Path(os.environ.get("ECOMMERCE_DIR", ROOT.parent / "ecommerce"))
    tree = ast.parse((src_dir / "gerador" / "clientes.py").read_text(encoding="utf-8"))
    ufs = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "UFS" for t in node.targets):
            ufs = ast.literal_eval(node.value)
    if ufs is None:
        raise RuntimeError("Tabela UFS não encontrada no gerador")
    rows = [
        {"uf": uf, "cidade": cidade, "cep5_min": faixa[0], "cep5_max": faixa[1]}
        for uf, (_peso, cidades, faixa, _ddds) in ufs.items()
        for cidade, _w in cidades
    ]
    df = pd.DataFrame(rows).sort_values(["uf", "cidade"]).reset_index(drop=True)
    df.to_csv(CONFIG_DIR / "mappings" / "cidades_referencia.csv", index=False)
    return df


if __name__ == "__main__":
    print(build_city_reference())
