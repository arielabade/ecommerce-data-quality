"""Toda regra de limpeza do código tem teste e aparece no catálogo (docs/CATALOG.md)."""

from __future__ import annotations

import re

from dq.settings import ROOT

EXPANSIONS = {
    "cidade_{m}": ["cidade_normalizacao", "cidade_sinonimo", "cidade_aproximacao"],
    "categoria_{m}": ["categoria_normalizacao", "categoria_sinonimo", "categoria_aproximacao"],
    "custo_real_{method}": ["custo_real_modelo", "custo_real_mediana_grupo"],
}
QUARANTINE_REASONS = ["atraso_fora_da_janela", "pedido_sem_cliente", "pai_em_quarentena", "item_sem_pedido",
                      "frete_sem_pedido", "endereco_sem_cliente", "data_irrecuperavel"]


def source_rules() -> set[str]:
    rules: set[str] = set()
    pat = re.compile(r'add_many\(\s*[a-z_"]+,\s*[^\n]*?,\s*"[^"]*",\s*f?"([a-z_{}]+)"', re.S)
    for f in (ROOT / "src" / "dq" / "clean").glob("*.py"):
        for m in pat.finditer(f.read_text(encoding="utf-8")):
            rules.update(EXPANSIONS.get(m.group(1), [m.group(1)]))
    return rules


def test_rule_extraction_finds_the_rules():
    r = source_rules()
    assert len(r) >= 40, sorted(r)
    assert {"duplicata_exata_hash", "bot_rajada_ip", "frete_implausivel_para_o_peso", "fuso_utc_segmento"} <= r


def test_every_rule_has_a_test():
    tests = (ROOT / "tests" / "test_rules.py").read_text(encoding="utf-8")
    missing = sorted(r for r in source_rules() | set(QUARANTINE_REASONS) if f'"{r}"' not in tests)
    assert not missing, f"regras sem teste: {missing}"


def test_every_rule_is_in_the_catalog():
    catalog = (ROOT / "docs" / "CATALOG.md").read_text(encoding="utf-8")
    missing = sorted(r for r in source_rules() | set(QUARANTINE_REASONS) if f"`{r}`" not in catalog)
    assert not missing, f"regras fora do catálogo: {missing}"
