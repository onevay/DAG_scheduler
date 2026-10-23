"""Создание политики по её настройкам из входа."""

from __future__ import annotations

from collections.abc import Callable

from expsched.contract.io_models import PolicyIn
from expsched.domain.snapshot import Policy
from expsched.policies.backfill import BackfillPolicy
from expsched.policies.fifo import FifoPolicy
from expsched.policies.preemption import PreemptivePolicy
from expsched.policies.priority import PriorityPolicy

_FACTORIES: dict[str, Callable[[PolicyIn], Policy]] = {
    "fifo": lambda config: FifoPolicy(),
    "fifo_skip": lambda config: FifoPolicy(skip=True),
    "priority": lambda config: PriorityPolicy(aging=config.aging),
    "priority_backfill": lambda config: BackfillPolicy(aging=config.aging),
}


def implemented_policies() -> list[str]:
    return sorted(_FACTORIES)


def make_policy(config: PolicyIn) -> Policy:
    """Политика по имени `config.name`; с `preemption="deadline_only"` — в надстройке вытеснения.

    Для ещё не реализованных имён и режимов — NotImplementedError.
    """
    factory = _FACTORIES.get(config.name)
    if factory is None:
        raise NotImplementedError(f"policy {config.name!r} is not implemented yet")
    if config.preemption == "never":
        return factory(config)
    if config.preemption != "deadline_only":
        raise NotImplementedError(f"preemption {config.preemption!r} is not implemented yet")
    return PreemptivePolicy(
        factory(config), config.max_preemptions_per_task, config.min_runtime_before_preempt
    )
