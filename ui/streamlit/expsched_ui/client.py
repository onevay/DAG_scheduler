"""Клиент REST API планировщика."""

from __future__ import annotations

from typing import Any

import httpx


class ApiUnavailable(RuntimeError):
    """API не отвечает или по адресу отвечает не он."""


class ApiClient:
    def __init__(self, base_url: str = "", http: httpx.Client | None = None, timeout: float = 60) -> None:
        # Клиент ходит прямо по адресу API: системный прокси (HTTP_PROXY) перехватил бы и локальные запросы.
        self._http = http or httpx.Client(base_url=base_url, timeout=timeout, trust_env=False)

    def policies(self) -> dict[str, Any]:
        return self._call("GET", "/v1/policies")[1]

    def validate(self, problem: dict[str, Any], mode: str) -> tuple[int, dict[str, Any]]:
        """(код ответа, тело): 200 — вход разобран, 422 — нарушена схема, 413 — слишком большой."""
        return self._call("POST", "/v1/validate", params={"mode": mode}, json=problem)

    def run(
        self, problem: dict[str, Any], policy: dict[str, Any] | None = None
    ) -> tuple[int, dict[str, Any]]:
        return self._call("POST", "/v1/runs", json={"problem": problem, "policy": policy})

    def _call(self, method: str, path: str, **kwargs: Any) -> tuple[int, dict[str, Any]]:
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.TransportError as error:
            raise ApiUnavailable(str(error)) from error
        try:
            return response.status_code, response.json()
        except ValueError as error:
            raise ApiUnavailable(f"{path}: ответ {response.status_code} не в формате JSON") from error
