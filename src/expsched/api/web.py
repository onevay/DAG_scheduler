"""Раздача собранного JS-клиента (`ui/web/dist`) тем же сервером, что и API.

Файлы сборки отдаются как есть, любой другой путь без расширения — `index.html`: маршруты клиента
(`/runs/<id>`, `/builder`, …) разбирает сам клиент. Пути `/v1/…` сюда не попадают — там ответ API.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse

# Скрипты только свои; стили встроенные (холст графа задаёт style у узлов) и шрифты Google из tokens.css
CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src 'self' https://fonts.gstatic.com",
        "img-src 'self' data: blob:",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'none'",
        "frame-ancestors 'none'",
    ]
)
# Имена в assets/ содержат хеш содержимого — их можно кешировать навсегда, index.html — нет
IMMUTABLE = "public, max-age=31536000, immutable"
API_PREFIXES = ("v1/", "docs", "redoc", "openapi.json")


def add_security_headers(app: FastAPI) -> None:
    """Заголовки для всех ответов: тип содержимого не угадывается, адрес страницы не уходит наружу."""

    @app.middleware("http")
    async def headers(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response


def mount_web(app: FastAPI, dist: Path, not_found: Callable[[str], Response]) -> None:
    """Подключает клиент из `dist`; `not_found(path)` — ответ на путь, которого нет ни в API, ни в сборке."""
    root = dist.resolve()
    index = root / "index.html"

    @app.get("/{path:path}", include_in_schema=False)
    def web(path: str) -> Response:
        if path.startswith(API_PREFIXES):
            return not_found(path)
        file = (root / path).resolve()
        if path and file.is_relative_to(root) and file.is_file():
            cache = IMMUTABLE if file.parent == root / "assets" else "no-cache"
            return FileResponse(file, headers=_page_headers(cache))
        if "." in path.rsplit("/", 1)[-1]:
            return not_found(path)  # пропавший файл сборки — не страница клиента
        return FileResponse(index, headers=_page_headers("no-cache"))


def _page_headers(cache: str) -> dict[str, str]:
    return {"Cache-Control": cache, "Content-Security-Policy": CSP, "X-Frame-Options": "DENY"}
