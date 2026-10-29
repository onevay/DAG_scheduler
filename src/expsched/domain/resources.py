"""Вектор ресурсов: неизменяемый объект-значение с покомпонентным порядком."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any


class Resources:
    """Неотрицательный целочисленный вектор по типам ресурсов (cpu, ram_mb, gpu, ...).

    Ресурс, которого нет в векторе, равен 0, поэтому нулевые компоненты не хранятся:
    `Resources({"gpu": 0}) == Resources()`. Ключи отсортированы, отсюда хешируемость
    и детерминированный обход.
    """

    __slots__ = ("_items",)

    _items: tuple[tuple[str, int], ...]

    def __init__(self, values: Mapping[str, int] | None = None) -> None:
        items: list[tuple[str, int]] = []
        for key, value in (values or {}).items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"resource {key!r} must be a non-negative integer, got {value!r}")
            if value:
                items.append((key, value))
        object.__setattr__(self, "_items", tuple(sorted(items)))

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("Resources is immutable")

    def __copy__(self) -> Resources:
        return self  # неизменяемый объект можно разделять

    def __deepcopy__(self, memo: dict[int, Any]) -> Resources:
        return self

    def get(self, key: str) -> int:
        for k, v in self._items:
            if k == key:
                return v
        return 0

    def keys(self) -> tuple[str, ...]:
        return tuple(k for k, _ in self._items)

    def items(self) -> tuple[tuple[str, int], ...]:
        return self._items

    def fits(self, capacity: Resources) -> bool:
        """Каждая компонента не больше, чем в `capacity`; два вектора могут быть несравнимы."""
        return all(v <= capacity.get(k) for k, v in self._items)

    def __add__(self, other: Resources) -> Resources:
        total = dict(self._items)
        for k, v in other._items:
            total[k] = total.get(k, 0) + v
        return Resources(total)

    def __sub__(self, other: Resources) -> Resources:
        """Разность векторов; если компонента уходит ниже нуля — ValueError."""
        diff = dict(self._items)
        for k, v in other._items:
            left = diff.get(k, 0) - v
            if left < 0:
                raise ValueError(f"resource {k!r} would become negative: {diff.get(k, 0)} - {v}")
            diff[k] = left
        return Resources(diff)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Resources) and self._items == other._items

    def __hash__(self) -> int:
        return hash(self._items)

    def __iter__(self) -> Iterator[str]:
        return iter(self.keys())

    def __bool__(self) -> bool:
        return bool(self._items)

    def __repr__(self) -> str:
        return "{" + ", ".join(f"{k}: {v}" for k, v in self._items) + "}"
