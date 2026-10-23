"""Клиент REST API планировщика."""

from __future__ import annotations

from typing import Any

import httpx


class ApiUnavailable(RuntimeError):
    """API не отвечает или по адресу отвечает не он."""


def error_message(status: int, body: dict[str, Any]) -> str:
    """Текст ошибки из тела `{"error": {"code", "message"}}`; для неожиданного ответа — код статуса."""
    error = body.get("error")
    return f"{error['code']}: {error['message']}" if isinstance(error, dict) else f"API ответил {status}"


class ApiClient:
    def __init__(self, base_url: str = "", http: httpx.Client | None = None, timeout: float = 60) -> None:
        # Клиент ходит прямо по адресу API: системный прокси (HTTP_PROXY) перехватил бы и локальные запросы.
        self._http = http or httpx.Client(base_url=base_url, timeout=timeout, trust_env=False)

    def policies(self) -> dict[str, Any]:
        return self._call("GET", "/v1/policies")[1]

    def validate(self, problem: dict[str, Any], mode: str) -> tuple[int, dict[str, Any]]:
        """(код ответа, тело): 200 — вход разобран; иначе ошибка, при нарушении схемы — с diagnostics."""
        return self._call("POST", "/v1/validate", params={"mode": mode}, json=problem)

    def run(
        self, problem: dict[str, Any], policy: dict[str, Any] | None = None
    ) -> tuple[int, dict[str, Any]]:
        """(код ответа, тело): 201 — `{run_id, result}`; иначе ошибка."""
        return self._call("POST", "/v1/runs", json={"problem": problem, "policy": policy})

    def events(self, run_id: str, page: int = 5000) -> list[dict[str, Any]]:
        """Весь журнал прогона, страница за страницей."""
        records: list[dict[str, Any]] = []
        from_seq: int | None = 0
        while from_seq is not None:
            status, body = self._call(
                "GET", f"/v1/runs/{run_id}/events", params={"from_seq": from_seq, "limit": page}
            )
            if status != 200:
                raise ApiUnavailable(error_message(status, body))
            records += body["events"]
            from_seq = body["next_seq"]
        return records

    def _call(self, method: str, path: str, **kwargs: Any) -> tuple[int, dict[str, Any]]:
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.TransportError as error:
            raise ApiUnavailable(str(error)) from error
        try:
            return response.status_code, response.json()
        except ValueError as error:
            raise ApiUnavailable(f"{path}: ответ {response.status_code} не в формате JSON") from error
