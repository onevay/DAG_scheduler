"""Проверка внешних событий входа."""

from __future__ import annotations

from expsched.domain.models import Event, EvType, Problem
from expsched.validation.diagnostics import Code, Diagnostic


def validate_events(problem: Problem) -> list[Diagnostic]:
    """E040: отказ или возврат узла, которого нет во входе."""
    return [
        Diagnostic(
            Code.E040,
            {"event": index, "type": event.type.value, "time": event.time, "worker": event.worker},
            "Такого узла нет во входе: исправьте id узла в событии или добавьте узел",
        )
        for index, event in enumerate(problem.events)
        if not knows_worker(problem, event)
    ]


def knows_worker(problem: Problem, event: Event) -> bool:
    """Событие узла ссылается на существующий узел; подача задачи узел не называет."""
    return event.type is EvType.SUBMIT or event.worker in problem.workers
