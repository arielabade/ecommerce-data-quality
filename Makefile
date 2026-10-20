# Ponta a ponta: make data corrupt etl evaluate report test
# Parâmetros em config/*.yaml; credenciais em .env (veja .env.example).
-include .env
export

PY ?= .venv/bin/python
ECOMMERCE_DIR ?= ../ecommerce
DQ_PROFILE ?= default
COMPOSE = docker compose --project-directory $(ECOMMERCE_DIR) -f $(ECOMMERCE_DIR)/docker-compose.yml

.PHONY: install source data snapshot profile corrupt etl evaluate report test test-e2e lint all clean small

install:            ## ambiente virtual com as dependências
	python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"

source:             ## (opcional) regera a base de origem do zero no repositório ../ecommerce
	$(MAKE) -C $(ECOMMERCE_DIR) dados recarregar

data:               ## sobe o MySQL de origem (só leitura), extrai as tabelas e faz o perfil
ifeq ($(DQ_PROFILE),default)
	$(COMPOSE) up -d --wait mysql
endif
	$(PY) -m dq.extract
	$(PY) -m dq.profile

snapshot:           ## refaz a amostra versionada data/sample a partir da extração completa
	$(PY) -m dq.extract snapshot

corrupt:            ## copia a base, injeta defeitos (seed fixo) e grava o registro de defeitos
	$(PY) -m dq.corrupt

etl:                ## raw -> staging -> clean -> marts, em incrementos, e confere a idempotência
	$(PY) -m dq.etl

evaluate:           ## precisão/recall por técnica contra o registro e impacto nas métricas
	$(PY) -m dq.evaluate

report:             ## relatório de qualidade, figuras do README e dashboard (docs/dashboard)
	$(PY) -m dq.report

test:               ## testes de unidade, contratos, guarda do gabarito e ponta a ponta (perfil pequeno)
	$(PY) -m pytest -q

test-e2e:
	$(PY) -m pytest -q -m e2e

lint:
	$(PY) -m ruff check src tests

small:              ## pipeline inteiro no perfil pequeno, sem MySQL
	DQ_PROFILE=small $(MAKE) data corrupt etl evaluate report

all: data corrupt etl evaluate report test

clean:              ## apaga dados gerados (a origem e a amostra ficam)
	rm -rf data/gold data/raw data/defect_log data/warehouse data/runs data/small
