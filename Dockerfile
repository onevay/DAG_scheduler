# Клиент и API одним образом: сборка клиента на Node, зависимости Python через uv, в итоговом образе — только
# Python, готовое окружение, сборка клиента и примеры. Запуск: docker compose up (порт только на 127.0.0.1).

FROM node:20-slim AS web
WORKDIR /src/ui/web
COPY ui/web/package.json ui/web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY ui/design /src/ui/design
COPY ui/web ./
RUN npm run build


FROM python:3.12-slim AS python
COPY --from=ghcr.io/astral-sh/uv:0.12.1 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Зависимости отдельным слоем: правка кода не пересобирает их
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --extra api --no-install-project
COPY src ./src
RUN uv sync --locked --extra api --no-editable


FROM python:3.12-slim
RUN useradd --system --uid 10001 --no-create-home expsched && mkdir /runs && chown expsched /runs
WORKDIR /app
COPY --from=python /app/.venv ./.venv
COPY --from=web /src/ui/web/dist ./web
COPY scenarios ./scenarios
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    EXPSCHED_WEB_DIR=/app/web \
    EXPSCHED_SCENARIOS_DIR=/app/scenarios \
    EXPSCHED_RUNS_DIR=/runs
VOLUME /runs
USER expsched
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --start-interval=2s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/v1/policies', timeout=4)"]
# Внутри контейнера слушаем все адреса — наружу порт открывает только проброс в compose.yaml
CMD ["uvicorn", "expsched.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
