"""Configuração do projeto: YAML versionado + variáveis de ambiente.

Papel: um único ponto que diz onde estão os dados de cada camada e quais
parâmetros cada etapa usa.
Entrada: ``config/default.yaml`` sobreposto pelo perfil (``DQ_PROFILE=small``
lê ``config/small.yaml``), ``config/corruption.yaml`` e ``config/cleaning.yaml``.
Saída: objeto ``Settings`` com caminhos absolutos e os dicionários de parâmetros.
Dependências: PyYAML. Credenciais só por ambiente (``DQ_SOURCE_URL`` ou
``MYSQL_HOST``/``MYSQL_PORT``/``MYSQL_USER``/``MYSQL_PASSWORD``/``MYSQL_DATABASE``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _read(name: str) -> dict:
    path = CONFIG_DIR / name
    return yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}


@dataclass(frozen=True)
class Settings:
    profile: str
    raw_config: dict
    corruption: dict
    cleaning: dict

    def path(self, key: str) -> Path:
        p = Path(self.raw_config["paths"][key])
        return p if p.is_absolute() else ROOT / p

    @property
    def source(self) -> dict:
        return self.raw_config["source"]

    @property
    def etl(self) -> dict:
        return self.raw_config["etl"]

    def truth_paths(self) -> list[Path]:
        """Caminhos que o código de limpeza nunca pode abrir (gabarito)."""
        return [self.path("defect_log"), self.path("gold")]

    def source_url(self) -> str:
        url = os.environ.get("DQ_SOURCE_URL")
        if url:
            return url
        missing = [v for v in ("MYSQL_USER", "MYSQL_PASSWORD") if not os.environ.get(v)]
        if missing:
            raise RuntimeError(
                f"Faltam variáveis de ambiente {missing}. Copie .env.example para .env "
                "(o Makefile carrega o arquivo) ou defina DQ_SOURCE_URL."
            )
        host = os.environ.get("MYSQL_HOST", "127.0.0.1")
        port = os.environ.get("MYSQL_PORT", "3336")
        db = os.environ.get("MYSQL_DATABASE", "tecidos")
        return f"mysql+pymysql://{os.environ['MYSQL_USER']}:{os.environ['MYSQL_PASSWORD']}@{host}:{port}/{db}?charset=utf8mb4"


@lru_cache(maxsize=4)
def get_settings(profile: str | None = None) -> Settings:
    profile = profile or os.environ.get("DQ_PROFILE", "default")
    cfg = _read("default.yaml")
    if profile != "default":
        cfg = _merge(cfg, _read(f"{profile}.yaml"))
    corruption = _merge(_read("corruption.yaml"), cfg.get("corruption_overrides", {}))
    cleaning = _merge(_read("cleaning.yaml"), cfg.get("cleaning_overrides", {}))
    return Settings(profile=profile, raw_config=cfg, corruption=corruption, cleaning=cleaning)
