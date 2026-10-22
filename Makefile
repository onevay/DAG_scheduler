# Работает в Linux и в Git Bash под Windows: все команды запускаются через uv.
RUN := uv run

.PHONY: setup lint types test test-all schema api

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

test-all:
	$(RUN) pytest

schema:
	$(RUN) python -m expsched.contract.io_models > schemas/problem.schema.json

api:
	$(RUN) uvicorn expsched.api.app:app --reload --port 8000
