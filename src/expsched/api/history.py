"""История наблюдённой работы задач — файл `estimates.json` рядом с прогонами.

Пополняется, когда прогон на агентах сохранён; по ней оценивается работа задач без длительности в следующих
прогонах. На ключ хранится не больше `LIMIT` последних наблюдений: задача меняется, старое устаревает.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from expsched.contract.io_models import ProblemIn
from expsched.estimate import Estimate
from expsched.estimate.history import Observation, estimates, observe

LIMIT = 20


class History:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()

    def load(self) -> dict[str, list[Observation]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return {k: [Observation(**o) for o in obs] for k, obs in raw["observations"].items()}
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            return {}  # нет истории или файл испорчен: оценки — по умолчаниям, файл перепишется

    def estimates(self, problem: ProblemIn) -> dict[str, Estimate]:
        return estimates(self.load(), problem)

    def record(self, problem: Mapping[str, Any], result: Mapping[str, Any]) -> None:
        """Наблюдения сохранённого прогона на агентах; вход — как его сохранило хранилище."""
        parsed = ProblemIn.model_validate(problem)
        if parsed.mode not in ("emulate", "shadow"):
            return
        seen = observe(parsed, result)
        if not seen:
            return
        with self.lock:
            history = self.load()
            for k, obs in seen.items():
                history[k] = [*history.get(k, []), *obs][-LIMIT:]
            body = {
                "version": 1,
                "observations": {k: [o.to_dict() for o in v] for k, v in sorted(history.items())},
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)
