# Работает в Linux и в Git Bash под Windows: Python-команды запускаются через uv, JS-клиент — через npm.
RUN := uv run
WEB := npm --prefix ui/web

.PHONY: setup lint types test perf test-all schema api ui web-install web-dev web-lint web-test web-build

setup:
	uv sync --all-extras
	$(RUN) pre-commit install

lint:
	$(RUN) ruff check .
	$(RUN) ruff format --check .
	$(RUN) lint-imports

types:
	$(RUN) mypy

test:
	$(RUN) pytest -m "not docker and not slow"

# Замеры по времени: на нагруженной машине могут не уложиться в границы, поэтому отдельно от test.
perf:
	$(RUN) pytest -m slow

test-all:
	$(RUN) pytest

schema:
	$(RUN) python -m expsched.contract.schemas schemas

api:
	$(RUN) uvicorn expsched.api.app:app --reload --port 8000

ui:
	$(RUN) streamlit run ui/streamlit/app.py

web-install:
	$(WEB) ci

# Интерфейс на http://localhost:5173; запросы /v1 уходят на API из `make api`.
web-dev:
	$(WEB) run dev

web-lint:
	$(WEB) run lint

web-test:
	$(WEB) test

web-build:
	$(WEB) run build
