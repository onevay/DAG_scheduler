"""Хранилище прогонов: каталог `<root>/<run_id>/` с problem.json, result.json, events.jsonl и summary.json.

`run_id` — начало `input_hash`: повторный запрос с тем же входом и политикой отдаёт сохранённый прогон.
В problem.json вход лежит вместе с политикой, которой он был посчитан.
Прогон пишется во временный каталог и переименовывается целиком: читатель не увидит наполовину записанный.
Когда прогонов больше `max_runs` или они вместе занимают больше `max_bytes`, удаляются самые старые.
summary.json — строка списка прогонов, чтобы список не читал result.json целиком. Прогон может исчезнуть между
проверкой и чтением (его вытеснил параллельный запрос) — чтение тогда бросает RunNotFound. Прогон, посчитанный
прежней версией формата результата, устарел: чтение бросает RunOutdated, в списке его нет.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from expsched.contract.hashing import canonical_json
from expsched.pipeline import SCHEMA_VERSION, RunResult

RUN_ID_PATTERN = r"^[0-9a-f]{16}$"


class RunNotFound(LookupError):
    """Прогона нет в хранилище: его не было или он вытеснен."""

    def __init__(self, run_id: str) -> None:
        super().__init__(run_id)
        self.run_id = run_id


class RunOutdated(RunNotFound):
    """Прогон посчитан прежней версией формата результата: его нужно посчитать заново."""


def run_id_of(input_hash: str) -> str:
    return input_hash.removeprefix("sha256:")[:16]


class RunStore:
    def __init__(self, root: Path, max_runs: int = 100, max_bytes: int | None = None) -> None:
        self.root = root
        self.max_runs = max_runs
        self.max_bytes = max_bytes

    def find(self, run_id: str) -> Path | None:
        path = self.root / run_id
        return path if (path / "result.json").is_file() else None

    def save(self, run_id: str, problem: dict[str, Any], outcome: RunResult) -> None:
        """Сохраняет прогон; если он уже сохранён (например, параллельным запросом), ничего не меняет."""
        self._write(run_id, problem, outcome)
        self._evict(keep=run_id)

    def result(self, run_id: str) -> dict[str, Any]:
        result = dict(json.loads(self._read(run_id, "result.json")))
        if result.get("schema_version") != SCHEMA_VERSION:
            raise RunOutdated(run_id)
        return result

    def discard(self, run_id: str) -> None:
        """Удаляет прогон, например устаревший перед пересчётом."""
        shutil.rmtree(self.root / run_id, ignore_errors=True)

    def problem(self, run_id: str) -> dict[str, Any]:
        return dict(json.loads(self._read(run_id, "problem.json")))

    def recent(self, limit: int) -> list[dict[str, Any]]:
        """Сводки последних прогонов, новые первыми."""
        summaries = []
        for path in self.root.iterdir() if self.root.is_dir() else []:
            if path.is_dir() and not path.name.startswith("."):
                try:
                    summary = json.loads((path / "summary.json").read_text(encoding="utf-8"))
                except FileNotFoundError:
                    continue  # вытеснен, пока шёл список
                if summary.pop("schema_version", None) == SCHEMA_VERSION:
                    summaries.append(summary)
        summaries.sort(key=lambda s: (s["created_at"], s["run_id"]), reverse=True)
        return summaries[:limit]

    def events(self, run_id: str, from_seq: int, limit: int) -> tuple[list[dict[str, Any]], int]:
        """Записи журнала с номерами [from_seq, from_seq + limit) и общее число записей."""
        records = self._read(run_id, "events.jsonl").splitlines()
        return [json.loads(line) for line in records[from_seq : from_seq + limit]], len(records)

    def _write(self, run_id: str, problem: dict[str, Any], outcome: RunResult) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{run_id}-", dir=self.root))
        (staging / "problem.json").write_text(json.dumps(problem, ensure_ascii=False), encoding="utf-8")
        (staging / "result.json").write_text(json.dumps(outcome.result, ensure_ascii=False), encoding="utf-8")
        events = "".join(canonical_json(r) + "\n" for r in outcome.events)
        (staging / "events.jsonl").write_text(events, encoding="utf-8", newline="\n")
        summary = _summary(run_id, problem, outcome.result)
        (staging / "summary.json").write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
        try:
            os.rename(staging, self.root / run_id)
        except OSError:
            shutil.rmtree(staging)  # тот же прогон уже сохранил параллельный запрос

    def _read(self, run_id: str, name: str) -> str:
        try:
            return (self.root / run_id / name).read_text(encoding="utf-8")
        except FileNotFoundError:
            raise RunNotFound(run_id) from None

    def _evict(self, keep: str) -> None:
        """Удаляет самые старые прогоны, пока их больше max_runs или они занимают больше max_bytes."""
        runs = sorted(
            (p for p in self.root.iterdir() if p.is_dir() and not p.name.startswith(".")),
            key=lambda p: (p.stat().st_mtime, p.name),
        )
        sizes = {p: _size(p) for p in runs}
        total = sum(sizes.values())
        for path in runs:
            over_count = len(runs) > self.max_runs
            over_size = self.max_bytes is not None and total > self.max_bytes
            if not (over_count or over_size):
                return
            if path.name == keep:
                continue
            shutil.rmtree(path, ignore_errors=True)
            runs = [p for p in runs if p != path]
            total -= sizes[path]


def _size(path: Path) -> int:
    try:
        return sum(f.stat().st_size for f in path.iterdir())
    except FileNotFoundError:
        return 0


def _summary(run_id: str, problem: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    metrics = result["metrics"] or {}
    return {
        "run_id": run_id,
        "name": problem.get("name"),
        "policy": result["policy"],
        "status": result["status"],
        "tasks": len(result["tasks"]),
        "makespan": metrics.get("makespan"),
        "created_at": datetime.now(UTC).isoformat(timespec="milliseconds"),
        "schema_version": result.get("schema_version"),
    }
