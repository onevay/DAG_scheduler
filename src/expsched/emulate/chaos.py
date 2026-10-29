"""Сбои напоказ: когда убить попытку и когда уронить узел — по seed, независимо от того, что идёт.

Промежутки между сбоями одного вида — экспоненциальные со средним `crash_every` (`node_down_every`), не
меньше единицы. Кого именно убить или какой узел уронить, решает доля `pick` среди тех, кто идёт в момент
сбоя, по порядку id: тот же seed и тот же ход прогона дают те же сбои.
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal

from expsched.contract.io_models import ChaosIn

Kind = Literal["crash", "down"]


@dataclass(frozen=True)
class Strike:
    t: int
    kind: Kind
    pick: float  # доля в [0, 1): какой по счёту из кандидатов

    def choose(self, candidates: list[str]) -> str | None:
        return candidates[int(self.pick * len(candidates))] if candidates else None


def _strikes(kind: Kind, every: int, seed: int) -> Iterator[Strike]:
    rng = random.Random(f"{seed}:{kind}")  # у каждого вида свой поток: сбои узлов не сдвигают убийства
    t = 0
    while True:
        t += max(1, round(rng.expovariate(1 / every)))
        yield Strike(t, kind, rng.random())


class Chaos:
    def __init__(self, chaos: ChaosIn) -> None:
        self.down_for = chaos.node_down_for
        self._streams: list[Iterator[Strike]] = []
        if chaos.crash_every:
            self._streams.append(_strikes("crash", chaos.crash_every, chaos.seed))
        if chaos.node_down_every:
            self._streams.append(_strikes("down", chaos.node_down_every, chaos.seed))
        self._next = [next(s) for s in self._streams]

    def due(self, now: int) -> list[Strike]:
        """Сбои с моментом не позже `now`, по времени; каждый отдаётся один раз."""
        out = []
        for i, stream in enumerate(self._streams):
            while self._next[i].t <= now:
                out.append(self._next[i])
                self._next[i] = next(stream)
        return sorted(out, key=lambda s: (s.t, s.kind))
