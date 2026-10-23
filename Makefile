# Работает в Linux и в Git Bash под Windows: все команды запускаются через uv.
RUN := uv run

.PHONY: setup lint types test perf test-all schema api ui

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
