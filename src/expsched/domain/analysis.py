"""Результат статического анализа графа (вычисляет `expsched.analysis.analyze`).

Лежит в domain, потому что входит в Snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Analysis:
    order: tuple[str, ...]  # топологический порядок задач, вошедших в анализ
    est: dict[str, int]  # самый ранний старт
    eft: dict[str, int]  # самое раннее окончание
    lft: dict[str, int | None]  # самое позднее окончание; None — дедлайнов ниже по графу нет
    lst: dict[str, int | None]  # самый поздний старт; запас задачи = lst - est
    rank: dict[str, int]  # длина критического пути до стока
    lb_path: int  # нижняя граница makespan по критическому пути
    lb_energy: dict[str, int]  # по суммарной загрузке каждого ресурса
    lb_thr: int  # по задачам, которым нужны крупные по GPU узлы
    lb: int  # итоговая нижняя граница: максимум из трёх
