"""Исключения движка. Они означают ошибку в программе, а не во входных данных."""

from __future__ import annotations

from expsched.domain.snapshot import Decision


class InvalidDecision(Exception):
    """Решение политики не прошло проверку: задача не готова, узел недоступен или не хватает ресурсов."""

    def __init__(self, decision: Decision, reason: str) -> None:
        super().__init__(f"{reason}: {decision}")
        self.decision = decision
