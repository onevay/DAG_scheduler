"""Встроенная нагрузка: «задача», которую агент запускает в контейнере, когда у задачи нет своего образа.

Делает EXPSCHED_WORK единиц работы по EXPSCHED_TIME_SCALE_MS мс, начиная с сохранённого: с
EXPSCHED_RESUME_FROM или с `checkpoint.json` в EXPSCHED_CHECKPOINT_DIR, если там больше. Каждые
EXPSCHED_CHECKPOINT_INTERVAL единиц и по сигналу SIGUSR1 сохраняет прогресс и всю работу; EXPSCHED_TOTAL —
вся работа, если её задала задача, а не планировщик (её длительность ему неизвестна); держит в памяти
EXPSCHED_USE_RAM_MB МиБ — так видно лимит памяти и нехватку её (OOM). Пустой EXPSCHED_CHECKPOINT_DIR —
без файлов: прогресс только в памяти процесса, его сохраняет и восстанавливает снимок CRIU. Только
стандартная библиотека: в образе нет ничего, кроме Python и этого файла.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path


def env(name: str, default: int = 0) -> int:
    return int(os.environ.get(name) or default)


def saved(directory: Path | None) -> int:
    if directory is None:
        return 0
    try:
        return int(json.loads((directory / "checkpoint.json").read_text(encoding="utf-8"))["progress"])
    except (FileNotFoundError, ValueError, KeyError, TypeError):
        return 0


def save(directory: Path, progress: int, total: int) -> None:
    tmp = directory / "checkpoint.json.tmp"
    tmp.write_text(json.dumps({"progress": progress, "total": total}), encoding="utf-8")
    tmp.replace(directory / "checkpoint.json")  # атомарно: агент не увидит недописанный файл


def main() -> int:
    path = os.environ.get("EXPSCHED_CHECKPOINT_DIR", "/checkpoint")
    directory = Path(path) if path else None
    if directory:
        directory.mkdir(parents=True, exist_ok=True)
    # EXPSCHED_TOTAL — вся работа задачи, которой не знает планировщик; без неё — то, что он прислал
    total = env("EXPSCHED_TOTAL") or env("EXPSCHED_RESUME_FROM") + env("EXPSCHED_WORK", 1)
    step = env("EXPSCHED_CHECKPOINT_INTERVAL")
    scale = env("EXPSCHED_TIME_SCALE_MS", 1000) / 1000
    ballast = bytearray(env("EXPSCHED_USE_RAM_MB") << 20)  # память занята, пока идёт работа
    ballast[::4096] = b"\x01" * len(range(0, len(ballast), 4096))  # по байту на страницу: память выделена
    progress = max(env("EXPSCHED_RESUME_FROM"), saved(directory))
    requested = False

    def on_signal(signum: int, frame: object) -> None:
        nonlocal requested
        requested = True

    if hasattr(signal, "SIGUSR1"):
        signal.signal(signal.SIGUSR1, on_signal)
    while progress < total:
        time.sleep(scale)
        progress += 1
        if directory and progress < total and ((step and progress % step == 0) or requested):
            save(directory, progress, total)
            requested = False
    del ballast
    print(f"done: {progress} units", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
