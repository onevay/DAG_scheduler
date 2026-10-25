# Работает в Linux и в Git Bash под Windows: Python-команды запускаются через uv, JS-клиент — через npm.
RUN := uv run
WEB := npm --prefix ui/web

.PHONY: setup lint types test perf test-all schema api ui web-install web-dev web-lint web-test web-build web-e2e

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
	$(RUN) python -m expsched.api.openapi schemas/openapi.json

api:
	$(RUN) --extra api uvicorn expsched.api.app:app --reload --port 8000

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

# Сквозные проверки в Chromium (ставится сам при первом запуске): свой API на :8010, клиент через vite preview на :4173
web-e2e:
	$(WEB) run e2e
