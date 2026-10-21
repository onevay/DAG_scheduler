"""Журнал прогона: всё, что произошло в симуляции, по порядку.

Каждая запись содержит seq (номер по порядку), t (время) и type. Результаты прогона строятся из журнала.
Журнал хранится в памяти и выгружается в JSON Lines.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from expsched.contract.hashing import canonical_hash, canonical_json


class Journal:
    def __init__(self) -> None:
        self._records: list[dict[str, Any]] = []

    def append(self, kind: str, t: int, **fields: Any) -> dict[str, Any]:
        if "seq" in fields:
            raise ValueError("seq is assigned by the journal")
        record = {"seq": len(self._records), "t": t, "type": kind, **fields}
        self._records.append(record)
        return record

    @property
    def records(self) -> Sequence[dict[str, Any]]:
        return self._records

    def __len__(self) -> int:
        return len(self._records)

    def result_hash(self) -> str:
        """Хеш всех записей. У двух прогонов одного входа он совпадает."""
        return canonical_hash(self._records)

    def write_jsonl(self, path: Path) -> None:
        lines = "".join(canonical_json(r) + "\n" for r in self._records)
        path.write_text(lines, encoding="utf-8", newline="\n")
