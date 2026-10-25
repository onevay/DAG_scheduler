# Работает в Linux и в Git Bash под Windows: Python-команды запускаются через uv, JS-клиент — через npm.
RUN := uv run
WEB := npm --prefix ui/web

.PHONY: setup lint types test perf test-all schema api ui web-install web-dev web-lint web-test web-build web-e2e audit audit-py audit-web

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

# Известные уязвимости зависимостей: Python — по uv.lock (pip-audit), JS — по package-lock.json, от high и выше
audit: audit-py audit-web

audit-py:
	uv export --locked --all-extras --no-hashes --no-emit-project -o .audit-requirements.txt
	uvx pip-audit -r .audit-requirements.txt --disable-pip --no-deps; status=$$?; rm -f .audit-requirements.txt; exit $$status

audit-web:
	$(WEB) audit --audit-level=high
