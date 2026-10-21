"""Граф зависимостей задач: структура данных и алгоритмы, без диагностик.

Представление: неизменяемые списки смежности в обе стороны (предшественники и последователи)
с отсортированными соседями. Обход всегда идёт по отсортированным ключам, поэтому результаты
детерминированы. Все алгоритмы O(V + E) и итеративны: графы до 10^5 вершин не упираются
в лимит рекурсии.

Рёбрами считаются только зависимости между задачами из переданного набора. Ссылки на
отсутствующие задачи (E010) и самоссылки (E011) в граф не попадают, дубликаты схлопываются:
о них сообщает валидация (validation/graph.py), а анализу и движку нужен чистый граф.
Благодаря этому тот же класс строит граф «оставшихся» задач, если передать набор без завершённых.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping, Sequence

from expsched.domain.models import Task


class TaskGraph:
    __slots__ = ("_nodes", "_pred", "_succ")

    def __init__(self, tasks: Mapping[str, Task]) -> None:
        # Ключи обходятся по возрастанию, поэтому списки последователей получаются отсортированными
        # без отдельной сортировки.
        nodes = sorted(tasks)
        succ: dict[str, list[str]] = {k: [] for k in nodes}
        pred: dict[str, tuple[str, ...]] = {}
        for key in nodes:
            deps = sorted({d for d in tasks[key].deps if d != key and d in tasks})
            pred[key] = tuple(deps)
            for dep in deps:
                succ[dep].append(key)
        self._nodes = tuple(nodes)
        self._succ = {k: tuple(v) for k, v in succ.items()}
        self._pred = pred

    @property
    def nodes(self) -> tuple[str, ...]:
        return self._nodes

    def __len__(self) -> int:
        return len(self._nodes)

    def preds(self, key: str) -> tuple[str, ...]:
        return self._pred[key]

    def succs(self, key: str) -> tuple[str, ...]:
        return self._succ[key]

    def topological_order(self) -> list[str]:
        """Алгоритм Кана. Если в графе есть цикл, порядок неполный: вершины цикла и всё за ним отсутствуют."""
        indegree = {k: len(self._pred[k]) for k in self._nodes}
        queue = deque(k for k in self._nodes if indegree[k] == 0)
        order: list[str] = []
        while queue:
            node = queue.popleft()
            order.append(node)
            for nxt in self._succ[node]:
                indegree[nxt] -= 1
                if indegree[nxt] == 0:
                    queue.append(nxt)
        return order

    def strongly_connected_components(self) -> list[list[str]]:
        """Итеративный алгоритм Тарьяна. Компоненты отсортированы внутри, порядок между ними не задан."""
        index: dict[str, int] = {}
        low: dict[str, int] = {}
        on_stack: set[str] = set()
        stack: list[str] = []
        components: list[list[str]] = []

        for root in self._nodes:
            if root in index:
                continue
            index[root] = low[root] = len(index)
            stack.append(root)
            on_stack.add(root)
            work = [(root, iter(self._succ[root]))]
            while work:
                node, neighbours = work[-1]
                descended = False
                for nxt in neighbours:
                    if nxt not in index:
                        index[nxt] = low[nxt] = len(index)
                        stack.append(nxt)
                        on_stack.add(nxt)
                        work.append((nxt, iter(self._succ[nxt])))
                        descended = True
                        break
                    if nxt in on_stack:
                        low[node] = min(low[node], index[nxt])
                if descended:
                    continue
                work.pop()
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[node])
                if low[node] == index[node]:
                    component: list[str] = []
                    while True:
                        member = stack.pop()
                        on_stack.discard(member)
                        component.append(member)
                        if member == node:
                            break
                    components.append(sorted(component))
        return components

    def cyclic_components(self) -> list[list[str]]:
        """Нетривиальные компоненты сильной связности, т. е. все циклы графа. Петель в графе нет."""
        if len(self.topological_order()) == len(self):  # типичный случай: цикла нет, Тарьян не нужен
            return []
        return sorted(c for c in self.strongly_connected_components() if len(c) > 1)

    def find_cycle(self, component: Sequence[str]) -> list[str]:
        """Один простой цикл внутри компоненты: [a, b, c, a]. Все циклы не перечисляются, их может быть
        экспоненциально много.

        Идём от минимальной вершины по минимальному последователю внутри компоненты, пока не вернёмся
        в уже пройденную вершину; хвост до входа в цикл отбрасывается.
        """
        members = set(component)
        node = min(members)
        path = [node]
        position = {node: 0}
        while True:
            nxt = next(s for s in self._succ[node] if s in members)
            if nxt in position:
                return [*path[position[nxt] :], nxt]
            position[nxt] = len(path)
            path.append(nxt)
            node = nxt

    def descendants(self, roots: Iterable[str]) -> set[str]:
        """Вершины, достижимые из roots путём длины >= 1. Сам корень входит, только если лежит на цикле."""
        seen: set[str] = set()
        stack = [s for root in roots for s in self._succ[root]]
        while stack:
            node = stack.pop()
            if node not in seen:
                seen.add(node)
                stack.extend(self._succ[node])
        return seen
