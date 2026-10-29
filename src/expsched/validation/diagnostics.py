"""Диагностики входа: ошибки (коды E) и предупреждения (коды W). Это данные результата, а не исключения."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal


class Code(StrEnum):
    E001 = "E001"  # входной файл не соответствует схеме
    E010 = "E010"  # зависимость от неизвестной задачи
    E011 = "E011"  # задача зависит от самой себя
    E012 = "E012"  # цикл зависимостей
    E020 = "E020"  # требование больше ёмкости любого подходящего узла
    E021 = "E021"  # нет узла, подходящего по меткам или по совокупности ресурсов
    E030 = "E030"  # дедлайн недостижим даже при бесконечных ресурсах
    E040 = "E040"  # событие для узла, которого нет во входе
    E050 = "E050"  # во время прогона не осталось работающего подходящего узла
    E070 = "E070"  # длительность задачи меньше 1
    W060 = "W060"  # дубликат ребра зависимости
    W080 = "W080"  # хаос может исчерпать повторы задачи
    W081 = "W081"  # хаос задан, но режим не на агентах


TITLES: dict[Code, str] = {
    Code.E001: "SCHEMA_INVALID",
    Code.E010: "UNKNOWN_DEPENDENCY",
    Code.E011: "SELF_DEPENDENCY",
    Code.E012: "DEPENDENCY_CYCLE",
    Code.E020: "RESOURCE_EXCEEDS_ANY_WORKER",
    Code.E021: "NO_MATCHING_WORKER",
    Code.E030: "DEADLINE_UNREACHABLE",
    Code.E040: "UNKNOWN_WORKER",
    Code.E050: "NO_LIVE_CAPACITY",
    Code.E070: "DURATION_NOT_POSITIVE",
    Code.W060: "DUPLICATE_EDGE",
    Code.W080: "CHAOS_FEW_RETRIES",
    Code.W081: "CHAOS_WITHOUT_AGENTS",
}

Level = Literal["error", "warning"]


@dataclass(frozen=True)
class Diagnostic:
    code: Code
    detail: dict[str, Any] = field(default_factory=dict)
    hint: str = ""

    @property
    def title(self) -> str:
        return TITLES[self.code]

    @property
    def level(self) -> Level:
        return "error" if self.code.startswith("E") else "warning"

    def to_dict(self) -> dict[str, Any]:
        """Сериализуемая форма для результата прогона."""
        return {"code": str(self.code), "level": self.level, "detail": self.detail, "hint": self.hint}


def has_errors(diagnostics: Iterable[Diagnostic]) -> bool:
    return any(d.level == "error" for d in diagnostics)
