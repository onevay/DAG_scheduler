"""Хранилище прогонов: каталог `<root>/<run_id>/` с problem.json, result.json, events.jsonl и summary.json.

`run_id` — начало `input_hash`: повторный запрос с тем же входом и политикой отдаёт сохранённый прогон.
В problem.json вход лежит вместе с политикой, которой он был посчитан.
Прогон пишется во временный каталог и переименовывается целиком: читатель не увидит наполовину записанный.
Когда прогонов больше `max_runs`, удаляются самые старые. summary.json — строка списка прогонов, чтобы список
не читал result.json целиком.
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
from expsched.pipeline import RunResult

RUN_ID_PATTERN = r"^[0-9a-f]{16}$"


def run_id_of(input_hash: str) -> str:
    return input_hash.removeprefix("sha256:")[:16]


class RunStore:
    def __init__(self, root: Path, max_runs: int = 100) -> None:
        self.root = root
        self.max_runs = max_runs

    def find(self, run_id: str) -> Path | None:
        path = self.root / run_id
        return path if (path / "result.json").is_file() else None

    def save(self, run_id: str, problem: dict[str, Any], outcome: RunResult) -> None:
        """Сохраняет прогон; если он уже сохранён (например, параллельным запросом), ничего не меняет."""
        self._write(run_id, problem, outcome)
        self._evict(keep=run_id)

    def result(self, run_id: str) -> dict[str, Any]:
        return dict(json.loads((self.root / run_id / "result.json").read_text(encoding="utf-8")))

    def problem(self, run_id: str) -> dict[str, Any]:
        return dict(json.loads((self.root / run_id / "problem.json").read_text(encoding="utf-8")))

    def recent(self, limit: int) -> list[dict[str, Any]]:
        """Сводки последних прогонов, новые первыми."""
        summaries = []
        for path in self.root.iterdir() if self.root.is_dir() else []:
            if path.is_dir() and not path.name.startswith(".") and (path / "summary.json").is_file():
                summaries.append(json.loads((path / "summary.json").read_text(encoding="utf-8")))
        summaries.sort(key=lambda s: (s["created_at"], s["run_id"]), reverse=True)
        return summaries[:limit]

    def events(self, run_id: str, from_seq: int, limit: int) -> tuple[list[dict[str, Any]], int]:
        """Записи журнала с номерами [from_seq, from_seq + limit) и общее число записей."""
        with (self.root / run_id / "events.jsonl").open(encoding="utf-8") as lines:
            records = lines.readlines()
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

    def _evict(self, keep: str) -> None:
        runs = sorted(
            (p for p in self.root.iterdir() if p.is_dir() and not p.name.startswith(".")),
            key=lambda p: (p.stat().st_mtime, p.name),
        )
        for path in runs[: max(0, len(runs) - self.max_runs)]:
            if path.name != keep:
                shutil.rmtree(path, ignore_errors=True)


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
    }
