.PHONY: setup db db-reset seed-demo clean-demo backfill-provenance test lint collect-once detect-once analyst-once analyst monitor review-polymarket dashboard logs

PYTHON ?= python3.12
VENV ?= .venv
VENV_PYTHON := $(VENV)/bin/python
VENV_RUFF := $(VENV)/bin/ruff
VENV_STREAMLIT := $(VENV)/bin/streamlit

setup:
	$(PYTHON) -m venv $(VENV)
	$(VENV_PYTHON) -m pip install --upgrade pip
	$(VENV_PYTHON) -m pip install -e ".[dev]"

db:
	docker compose up -d db

db-reset:
	$(VENV_PYTHON) -m fuck_inside_traders.scripts.manage_db db-reset

seed-demo:
	$(VENV_PYTHON) -m fuck_inside_traders.scripts.manage_db seed-demo

clean-demo:
	$(VENV_PYTHON) -m fuck_inside_traders.scripts.manage_db clean-demo

backfill-provenance:
	$(VENV_PYTHON) -m fuck_inside_traders.scripts.manage_db backfill-provenance

test:
	$(VENV_PYTHON) -m pytest

lint:
	$(VENV_RUFF) check .

collect-once:
	$(VENV_PYTHON) -m fuck_inside_traders.scripts.run_collectors --once

detect-once:
	$(VENV_PYTHON) -m fuck_inside_traders.scripts.run_detector --once

analyst-once:
	$(VENV_PYTHON) -m fuck_inside_traders.scripts.run_analyst --once

analyst:
	$(VENV_PYTHON) -m fuck_inside_traders.scripts.run_analyst $(if $(INTERVAL),--interval-minutes $(INTERVAL),)

monitor:
	$(VENV_PYTHON) -m fuck_inside_traders.scripts.monitor $(if $(INTERVAL),--interval-minutes $(INTERVAL),)

review-polymarket:
	$(VENV_PYTHON) -m fuck_inside_traders.scripts.review_polymarket_candidates $(if $(TOPIC),--topic $(TOPIC),)

dashboard:
	STREAMLIT_BROWSER_GATHER_USAGE_STATS=false $(VENV_STREAMLIT) run fuck_inside_traders/dashboard/streamlit_app.py --server.headless true

logs:
	tail -n 200 logs/app.log
