"""Настройки агента: по умолчанию < файл `agent.toml` < параметры командной строки.

В файле — таблица `[agent]` с теми же именами, что у параметров (`ram_mb`, `allow_images`, …); неизвестное
имя — ошибка, а не молчаливо пропущенная опечатка. Токен — из EXPSCHED_AGENT_TOKEN или из файла `token_file`
(доступного только агенту), но не из самого конфига: его читают шире.
"""

from __future__ import annotations

import json
import logging
import os
import random
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

RuntimeName = Literal["docker", "podman", "fake", "gravesentinel"]
SOCKET = "/run/gravesentinel/hypervisor.sock"


@dataclass(frozen=True)
class Settings:
    id: str = ""
    scheduler: str = "http://127.0.0.1:8000"
    runtime: RuntimeName = "docker"
    workload_image: str | None = None
    socket: str = SOCKET
    hypervisor_uid: int = 0
    state: Path = Path(".expsched-agent")
    cpu: int | None = None
    ram_mb: int | None = None
    gpu: int | None = None
    max_containers: int = 4
    allow_images: tuple[str, ...] = ()
    token_file: Path | None = None
    log_format: Literal["text", "json"] = "text"
    stop_on_exit: bool = False  # остановить свои контейнеры при выходе; иначе их подхватит следующий запуск
    retry_max_s: float = 60  # предел паузы между попытками связаться с планировщиком

    def token(self) -> str | None:
        if token := os.environ.get("EXPSCHED_AGENT_TOKEN"):
            return token
        if self.token_file is not None:
            return self.token_file.read_text(encoding="utf-8").strip() or None
        return None


NAMES = {f.name for f in fields(Settings)}
PATHS = {"state", "token_file"}


def settings(file: Path | None, overrides: Mapping[str, Any]) -> Settings:
    """Значения файла поверх умолчаний, заданные в командной строке — поверх файла."""
    values: dict[str, Any] = {}
    if file is not None:
        table = tomllib.loads(file.read_text(encoding="utf-8")).get("agent", {})
        unknown = set(table) - NAMES
        if unknown:
            raise ValueError(
                f"{file}: unknown settings {', '.join(sorted(unknown))}; known: {', '.join(sorted(NAMES))}"
            )
        values.update(table)
    values.update({k: v for k, v in overrides.items() if v is not None})
    for name in PATHS & set(values):
        values[name] = Path(values[name])
    if "allow_images" in values:
        values["allow_images"] = tuple(values["allow_images"])
    result = replace(Settings(), **values)
    if not result.id:
        raise ValueError("agent id is required: --id or `id` in [agent]")
    if result.runtime not in ("docker", "podman", "fake", "gravesentinel"):
        raise ValueError(f"unknown runtime {result.runtime!r}")
    return result


class Backoff:
    """Пауза перед новой попыткой связи: 1, 2, 4 … до предела, с разбросом ±20 %, чтобы агенты, потерявшие
    сервер одновременно, не возвращались хором. Удачный обмен сбрасывает её."""

    def __init__(self, maximum: float = 60, jitter: Callable[[], float] = random.random) -> None:
        self.maximum, self.jitter = maximum, jitter
        self.failures = 0

    def next(self) -> float:
        base = min(self.maximum, 2.0**self.failures)
        self.failures += 1
        return base * (0.8 + 0.4 * self.jitter())

    def reset(self) -> None:
        self.failures = 0


class JsonFormatter(logging.Formatter):
    """Запись журнала — строка JSON: удобно собирать journald, Loki и им подобным."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "at": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            entry["error"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def configure_logging(log_format: Literal["text", "json"]) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        JsonFormatter()
        if log_format == "json"
        else logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    )
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
