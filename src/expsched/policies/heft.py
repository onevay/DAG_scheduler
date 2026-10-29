"""Списочные политики для разнородных узлов: HEFT и PEFT, в динамическом варианте.

HEFT (Topcuoglu и др., 2002): ранг задачи вверх — её средняя по узлам длительность плюс самый длинный ранг
потомков; задачи идут по убыванию ранга, каждая — на узел с самым ранним окончанием (EFT). Длительность на
узле — работа задачи на его скорости; у работы с разбросом окончание идущих попыток берётся по квантилю 0,8,
как в резервах backfill (стохастический HEFT). Связи между задачами ничего не стоят: данные общие.

PEFT (Arabnejad, Barbosa, 2014) смотрит на шаг вперёд: OCT(t, w) — оптимистичная длина пути от задачи до
конца, если она идёт на узле w; ранг — среднее OCT по узлам, узел — минимум EFT + OCT.

План пересчитывается на каждой эпохе по текущему снимку. Лучший узел задачи занят — она ждёт его; первая
такая задача получает резерв, остальные стартуют, только если не отодвигают его (как в backfill).
"""

from __future__ import annotations

from collections.abc import Mapping

from expsched.domain.models import time_for
from expsched.domain.resources import Resources
from expsched.domain.snapshot import Decision, Snapshot
from expsched.domain.states import FINAL_STATUSES
from expsched.policies.backfill import Reservation, earliest


class HeftPolicy:
    name = "heft"

    def decide(self, snapshot: Snapshot) -> list[Decision]:
        cost = _costs(snapshot)
        rank = self.ranks(snapshot, cost)
        free = dict(snapshot.free)
        started: list[tuple[str, str]] = []
        held: Reservation | None = None
        out: list[Decision] = []
        order = sorted(snapshot.ready, key=lambda k: (-rank[k], -snapshot.tasks[k].priority, k))
        for task_id in order:
            best = self.place(snapshot, task_id, cost, free, started)
            if best is None:
                continue  # нет работающего допустимого узла, куда задача поместится
            worker, start, finish, extra = best
            if start > snapshot.t:  # лучший узел занят: ждать его
                if held is None:
                    held = Reservation(task_id, worker, start, extra)
                    out.append(Decision("RESERVE", task_id, worker, {"t_res": start}))
                continue
            if held is not None and worker == held.worker and finish > held.t:
                need = snapshot.tasks[task_id].resources
                if not need.fits(held.extra):
                    continue  # отодвинула бы резерв
                held.extra = held.extra - need
            free[worker] = free[worker] - snapshot.tasks[task_id].resources
            started.append((task_id, worker))
            reason = {"code": self.name.upper(), "rank": round(rank[task_id], 3), "eft": finish}
            out.append(Decision("START", task_id, worker, reason))
        return out

    def ranks(self, snapshot: Snapshot, cost: Mapping[str, Mapping[str, int]]) -> dict[str, float]:
        """Ранг вверх: средняя длительность по узлам плюс наибольший ранг потомка."""
        children = _children(snapshot)
        rank: dict[str, float] = {}
        for task_id in _reverse_topological(snapshot, children):
            mean = sum(cost[task_id].values()) / len(cost[task_id]) if cost[task_id] else 0
            rank[task_id] = mean + max((rank[c] for c in children[task_id] if c in rank), default=0)
        return rank

    def place(
        self,
        snapshot: Snapshot,
        task_id: str,
        cost: Mapping[str, Mapping[str, int]],
        free: Mapping[str, Resources],
        started: list[tuple[str, str]],
    ) -> tuple[str, int, int, Resources] | None:
        """Узел с самым ранним окончанием (при равенстве — с меньшим id): узел, старт, окончание, остаток."""
        best: tuple[float, str, int, int, Resources] | None = None
        for worker in snapshot.eligible[task_id]:
            if not snapshot.up[worker]:
                continue
            found = earliest(snapshot, task_id, worker, free, started)
            if found is None:
                continue
            start, extra = found
            finish = start + cost[task_id][worker]
            score = self.score(task_id, worker, finish)
            if best is None or (score, worker) < (best[0], best[1]):
                best = (score, worker, start, finish, extra)
        return None if best is None else best[1:]

    def score(self, task_id: str, worker: str, finish: int) -> float:
        return finish


class PeftPolicy(HeftPolicy):
    name = "peft"

    def ranks(self, snapshot: Snapshot, cost: Mapping[str, Mapping[str, int]]) -> dict[str, float]:
        """Ранг — среднее OCT по узлам; OCT запоминается для выбора узла."""
        children = _children(snapshot)
        self.oct: dict[str, dict[str, float]] = {}
        for task_id in _reverse_topological(snapshot, children):
            self.oct[task_id] = {
                w: max(
                    (
                        min(self.oct[c][v] + cost[c][v] for v in cost[c])
                        for c in children[task_id]
                        if c in self.oct and cost[c]
                    ),
                    default=0,
                )
                for w in cost[task_id]
            }
        return {k: sum(o.values()) / len(o) if o else 0 for k, o in self.oct.items()}

    def score(self, task_id: str, worker: str, finish: int) -> float:
        return finish + self.oct[task_id].get(worker, 0)


def _costs(snapshot: Snapshot) -> dict[str, dict[str, int]]:
    """Длительность остатка каждой незаконченной задачи на каждом её допустимом узле."""
    return {
        k: {w: time_for(snapshot.rem[k], snapshot.speed.get(w, 1)) for w in snapshot.eligible[k]}
        for k, status in snapshot.status.items()
        if status not in FINAL_STATUSES and k not in snapshot.running
    }


def _children(snapshot: Snapshot) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {k: [] for k in snapshot.tasks}
    for k, task in snapshot.tasks.items():
        for dep in task.deps:
            if dep in out:
                out[dep].append(k)
    return out


def _reverse_topological(snapshot: Snapshot, children: Mapping[str, list[str]]) -> list[str]:
    """Незаконченные задачи от листьев к корням (потомки раньше предков)."""
    alive = {k for k, s in snapshot.status.items() if s not in FINAL_STATUSES and k not in snapshot.running}
    pending = {k: sum(1 for c in children[k] if c in alive) for k in alive}
    ready = sorted(k for k, n in pending.items() if n == 0)
    order: list[str] = []
    while ready:
        k = ready.pop()
        order.append(k)
        for dep in snapshot.tasks[k].deps:
            if dep in pending:
                pending[dep] -= 1
                if pending[dep] == 0:
                    ready.append(dep)
    return order + sorted(alive - set(order))  # остаток — в цикле; карантин их не пустит, но ранг нужен
