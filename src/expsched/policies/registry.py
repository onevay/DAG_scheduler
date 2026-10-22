"""Создание политики по её настройкам из входа."""

from __future__ import annotations

from collections.abc import Callable

from expsched.contract.io_models import PolicyIn
from expsched.domain.snapshot import Policy
from expsched.policies.fifo import FifoPolicy
from expsched.policies.priority import PriorityPolicy

_FACTORIES: dict[str, Callable[[PolicyIn], Policy]] = {
    "fifo": lambda config: FifoPolicy(),
    "fifo_skip": lambda config: FifoPolicy(skip=True),
    "priority": lambda config: PriorityPolicy(aging=config.aging),
}


def implemented_policies() -> list[str]:
    return sorted(_FACTORIES)


def make_policy(config: PolicyIn) -> Policy:
    """Политика по имени `config.name`. Для ещё не реализованных имён — NotImplementedError."""
    factory = _FACTORIES.get(config.name)
    if factory is None:
        raise NotImplementedError(f"policy {config.name!r} is not implemented yet")
    return factory(config)
