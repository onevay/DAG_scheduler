"""Результат статического анализа графа. Считает его `expsched.analysis.analyze`.

Тип лежит в domain, потому что входит в Snapshot, который видят политики: они не должны знать ни о движке,
ни о пакете, вычисляющем анализ.
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
    lb_path: int
    lb_energy: dict[str, int]
    lb_thr: int
    lb: int  # нижняя граница makespan: максимум из трёх
