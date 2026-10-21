"""Общие части политик: выбор узла для задачи."""

from __future__ import annotations

from collections.abc import Mapping
from fractions import Fraction

from expsched.domain.resources import Resources
from expsched.domain.snapshot import Snapshot


def best_fit(snapshot: Snapshot, key: str, free: Mapping[str, Resources]) -> str | None:
    """Самый тесный узел для задачи или None, если она сейчас никуда не помещается.

    `free` — свободные ресурсы с учётом решений, уже принятых политикой в этой эпохе. Узлы сравниваются
    по остатку доминирующего ресурса (которого задача просит больше всего относительно максимума свободного),
    затем по сумме остатков ресурсов задачи, затем по id. Так крупные блоки остаются крупным задачам.
    """
    need = snapshot.tasks[key].resources
    candidates = [w for w in snapshot.eligible[key] if snapshot.up[w] and need.fits(free[w])]
    if not candidates:
        return None
    if not need:
        return candidates[0]  # задача без ресурсов ничего не занимает: подходит любой узел

    largest = {r: max(f.get(r) for f in free.values()) for r in need}
    dominant = max(need, key=lambda r: (Fraction(need.get(r), max(1, largest[r])), r))
    return min(
        candidates,
        key=lambda w: (
            free[w].get(dominant) - need.get(dominant),
            sum(free[w].get(r) - need.get(r) for r in need),
            w,
        ),
    )
