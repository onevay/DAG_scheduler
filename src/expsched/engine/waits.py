"""Почему готовая задача не стартовала: код причины и свидетель для журнала."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from expsched.domain.resources import Resources
from expsched.engine.state import SimState


def ready_wait(
    state: SimState,
    key: str,
    free_before: Mapping[str, Resources],
    started: Sequence[str],
    reserved: Mapping[str, tuple[str, int]] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Причина ожидания готовой задачи после решений эпохи; проверяется по порядку, первая подходящая.

    WAIT_WORKER_DOWN — ни один допустимый узел не работает; WAIT_RESOURCES — задача не помещалась ни на один
    живой узел ещё до решений эпохи; WAIT_RESERVATION — в эпохе есть резерв, а задача помещается и после
    решений, то есть её придержали ради резерва; WAIT_PRIORITY — место было, но политика отдала его другим.
    `free_before` — свободные ресурсы до решений эпохи, `started` — задачи, стартовавшие в эту эпоху,
    `reserved` — резервы эпохи (задача -> (узел, t_res)) в порядке решений.
    """
    eligible = state.eligible[key]
    live = [w for w in eligible if state.up[w]]
    if not live:
        return "WAIT_WORKER_DOWN", {"eligible": list(eligible)}
    need = state.tasks[key].resources
    if not any(need.fits(free_before[w]) for w in live):
        # Ближе всего к нужному — узел, где свободно больше всего из того, что просит задача.
        best = min(live, key=lambda w: (-sum(min(free_before[w].get(r), need.get(r)) for r in need), w))
        return "WAIT_RESOURCES", {"need": dict(need.items()), "best": {best: dict(free_before[best].items())}}
    if reserved and key not in reserved and any(need.fits(state.free[w]) for w in live):
        head, (worker, t_res) = next(iter(reserved.items()))
        return "WAIT_RESERVATION", {"head": head, "worker": worker, "t_res": t_res}
    return "WAIT_PRIORITY", {"started": list(started)}
