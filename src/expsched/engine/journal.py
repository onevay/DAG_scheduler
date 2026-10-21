"""Журнал прогона (events.jsonl): первичный источник истины.

Каждая запись — словарь с обязательными полями seq (монотонный номер), t (время) и type.
Результат, метрики, объяснения и верификатор строятся только из журнала.
Пока журнал хранится в памяти, как в эталоне; потоковая запись — задача промышленной версии.
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
        """Хеш канонического JSON журнала: два прогона одного входа обязаны его совпасть."""
        return canonical_hash(self._records)

    def write_jsonl(self, path: Path) -> None:
        lines = "".join(canonical_json(r) + "\n" for r in self._records)
        path.write_text(lines, encoding="utf-8", newline="\n")
