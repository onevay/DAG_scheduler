"""Создание политики по её настройкам из входа."""

from __future__ import annotations

import importlib.util
from collections.abc import Callable

from expsched.contract.io_models import PolicyIn
from expsched.domain.snapshot import Policy
from expsched.policies.backfill import BackfillPolicy
from expsched.policies.fifo import FifoPolicy
from expsched.policies.preemption import PreemptivePolicy
from expsched.policies.priority import PriorityPolicy


def _cpsat(config: PolicyIn, seed: int) -> Policy:
    from expsched.policies.cpsat.policy import CpSatPolicy  # ortools — необязательная зависимость

    return CpSatPolicy(config.objective, config.time_limit, seed)


_FACTORIES: dict[str, Callable[[PolicyIn, int], Policy]] = {
    "fifo": lambda config, seed: FifoPolicy(),
    "fifo_skip": lambda config, seed: FifoPolicy(skip=True),
    "priority": lambda config, seed: PriorityPolicy(aging=config.aging),
    "priority_backfill": lambda config, seed: BackfillPolicy(aging=config.aging),
}
if importlib.util.find_spec("ortools") is not None:
    _FACTORIES["cpsat"] = _cpsat


def implemented_policies() -> list[str]:
    return sorted(_FACTORIES)


def make_policy(config: PolicyIn, seed: int = 0) -> Policy:
    """Политика по имени `config.name`; с вытеснением `deadline_only` или `priority` — в его надстройке.

    Для ещё не реализованных имён (cpsat без установленного ortools) — NotImplementedError.
    `seed` — сид входа: им пользуется cpsat.
    """
    factory = _FACTORIES.get(config.name)
    if factory is None:
        raise NotImplementedError(f"policy {config.name!r} is not implemented yet")
    if config.preemption == "never":
        return factory(config, seed)
    return PreemptivePolicy(
        factory(config, seed),
        config.max_preemptions_per_task,
        config.min_runtime_before_preempt,
        "priority" if config.preemption == "priority" else "deadline",
    )
