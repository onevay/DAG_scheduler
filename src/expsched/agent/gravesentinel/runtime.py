"""Среда запуска gravesentinel: попытки — контейнеры на парах CPU, чекпоинты — снимки процесса CRIU.

Логический контейнер — задача прогона (`run.task`): поколения чекпоинтов переживают попытки, и повтор
задачи — восстановление процесса из последнего снимка, а прежний экземпляр гипервизор сначала убирает
(fence). Холодный старт в гипервизоре без монтирований и лимитов памяти: корень только для чтения, сети
нет, контейнеру — один CPU пары. Поэтому задача с GPU или больше чем одним CPU отклоняется, а сохранённая
работа не читается из файла, а считается: база (сохранённое прежними попытками) плюс время с запуска в
масштабе прогона. Снимок делается после этого отсчёта, так что восстановленный процесс не позади
сообщённого — оценка снизу. Чекпоинты на границах `checkpoint_interval` и по команде запрашивает сама
среда; сохранённую работу пишет в `checkpoint.json` задачи, откуда её берёт агент.

Состояние среды (попытки, пары CPU, операции гипервизора) — в `gravesentinel.json` каталога агента:
после перезапуска агента ожидающие операции досматриваются, а после перезапуска демона регистрации
повторяются.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from expsched.agent.gravesentinel.client import (
    CPU_PAIRS,
    Client,
    ColdStart,
    HypervisorError,
    Registration,
    Submit,
)
from expsched.agent.runtime import State, write_progress
from expsched.contract.agent import StartCommand, Usage

log = logging.getLogger("expsched.agent")
WORKLOAD_IMAGE = "localhost/expsched-workload:local"


@dataclass
class _Attempt:
    container_id: str
    runtime_container: str
    pair: tuple[int, int]
    image: str
    argv: list[str]
    env: dict[str, str]
    checkpoint_dir: str
    scale_ms: int
    interval: int  # 0 — без чекпоинтов по интервалу
    total: int  # вся работа задачи, в единицах модели
    base: int  # сохранено к запуску этой попытки
    saved: int  # последний удавшийся чекпоинт
    operation: str | None = None  # идущая операция гипервизора
    pending: Literal["start", "checkpoint", "stop"] | None = None
    pending_progress: int = 0  # что сохранит идущий чекпоинт
    started_at: float | None = None  # когда процесс запущен — по часам стены
    requested: bool = False  # чекпоинт по команде
    failed: int | None = None  # запуск не удался — код выхода для агента


class GravesentinelRuntime:
    name: Literal["gravesentinel"] = "gravesentinel"

    def __init__(
        self,
        client: Client,
        state_dir: Path,
        workload_image: str = WORKLOAD_IMAGE,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        stop_timeout_s: float = 60,
    ) -> None:
        self.client = client
        self.workload_image = workload_image
        self.clock, self.sleep, self.stop_timeout_s = clock, sleep, stop_timeout_s
        self.file = state_dir / "gravesentinel.json"
        state_dir.mkdir(parents=True, exist_ok=True)
        self.attempts: dict[str, _Attempt] = {}
        if self.file.exists():
            for key, fields in json.loads(self.file.read_text(encoding="utf-8")).items():
                self.attempts[key] = _Attempt(**{**fields, "pair": tuple(fields["pair"])})

    # ---- Runtime ----

    def start(self, command: StartCommand, checkpoint_dir: Path) -> str:
        if command.limits.get("gpu"):
            raise RuntimeError("gravesentinel runs CPU-only containers")
        if command.limits.get("cpu", 1) > 1:
            raise RuntimeError("a gravesentinel container gets one CPU of its pair; the task needs more")
        busy = {a.pair for a in self.attempts.values()}
        free = [p for p in CPU_PAIRS if p not in busy]
        if not free:
            raise RuntimeError("all four CPU pairs are busy")
        env = {
            "EXPSCHED_CHECKPOINT_DIR": "",  # чекпоинт — снимок процесса; корень только для чтения
            "EXPSCHED_RESUME_FROM": str(command.resume_from),
            "EXPSCHED_WORK": str(command.work),
            "EXPSCHED_TIME_SCALE_MS": str(command.time_scale_ms),
        }
        image, argv = self.workload_image, []
        if command.container:
            env.update(command.container.env)
            image, argv = command.container.image, list(command.container.command)
        a = _Attempt(
            container_id=f"{command.run_id}.{command.task}",
            runtime_container=f"expsched-{command.run_id}-{command.task}",
            pair=free[0],
            image=image,
            argv=argv,
            env=env,
            checkpoint_dir=str(checkpoint_dir),
            scale_ms=command.time_scale_ms,
            interval=command.checkpoint_interval or 0,
            total=command.resume_from + command.work,
            base=command.resume_from,
            saved=command.resume_from,
        )
        self.client.register(self._registration(a))
        self.attempts[command.key] = a
        self._submit(command.key, a, "SubmitRestoreOrRestart", "start")
        return a.runtime_container

    def stop(self, key: str) -> None:
        """Остановить и убрать контейнер, освободить пару CPU; поколения чекпоинтов остаются."""
        a = self.attempts.get(key)
        if a is None:
            return
        if a.pending != "stop":
            self._submit(key, a, "SubmitStopContainer", "stop")
        deadline = self.clock() + self.stop_timeout_s
        while not self._settled(a) and self.clock() < deadline:
            self.sleep(0.2)
        while True:
            try:
                self.client.unregister(a.container_id)
                break
            except HypervisorError:
                try:
                    self.client.status(a.container_id)
                except HypervisorError:
                    break  # регистрации уже нет: демон перезапускался
                if self.clock() >= deadline:
                    log.warning("gravesentinel keeps %s registered: its CPU pair stays taken", a.container_id)
                    break
                self.sleep(0.5)  # гипервизор обслуживает поколения — снятие откладывается
        del self.attempts[key]
        self._save()

    def checkpoint(self, key: str) -> None:
        if key in self.attempts:
            self.attempts[key].requested = True
            self._save()

    def inspect(self, key: str) -> State:
        a = self.attempts.get(key)
        if a is None:
            return State("missing")
        if a.failed is not None:
            return State("exited", a.failed)
        if a.pending == "start" and not self._settled(a):
            return State("running")  # гипервизор ещё восстанавливает или запускает
        if a.failed is not None:
            return State("exited", a.failed)
        try:
            status = self.client.status(a.container_id)
        except HypervisorError:  # демон перезапустился: регистрации у него больше нет
            self.client.register(self._registration(a))
            status = self.client.status(a.container_id)
        if status.state == "absent":
            return State("missing")
        if status.state == "exited":
            return State("oom") if status.oom_killed else State("exited", status.exit_code)
        self._drive(key, a)
        return State("running")

    def keys(self) -> list[str]:
        return sorted(self.attempts)

    def usage(self, keys: list[str]) -> dict[str, Usage]:
        return {}  # контейнеры у rootful Podman: агенту без root их потребление не видно

    def logs(self, key: str) -> list[str]:
        return []  # у гипервизора нет журнала контейнера, а `podman logs` агенту без root недоступен

    # ---- чекпоинты и операции ----

    def progress(self, a: _Attempt) -> int:
        """Сколько работы сделано сейчас — оценка снизу по времени с запуска."""
        if a.started_at is None:
            return a.base
        done = int((self.clock() - a.started_at) * 1000) // a.scale_ms
        return min(a.total, a.base + done)

    def _drive(self, key: str, a: _Attempt) -> None:
        if a.pending == "checkpoint" and not self._settled(a):
            return
        now = self.progress(a)
        due = a.interval and now // a.interval > a.saved // a.interval
        if (due or a.requested) and a.saved < now < a.total:
            a.requested = False
            a.pending_progress = now
            self._submit(key, a, "SubmitCreateCheckpoint", "checkpoint")

    def _settled(self, a: _Attempt) -> bool:
        """Идущая операция закончилась (или её нет); её итог применён к попытке."""
        if a.operation is None:
            return True
        op = self.client.operation(a.operation)
        if not op.done:
            return False
        kind, a.operation, a.pending = a.pending, None, None
        if op.state != "succeeded":
            log.warning("gravesentinel %s of %s: %s (%s)", kind, a.container_id, op.state, op.error_code)
        if kind == "start":
            if op.state == "succeeded":
                a.started_at = self.clock()
            else:
                a.failed = 125  # как у podman run: контейнер не запустился
        elif kind == "checkpoint" and op.state == "succeeded":
            a.saved = a.pending_progress
            write_progress(Path(a.checkpoint_dir), a.saved)
        self._save()
        return True

    def _submit(
        self, key: str, a: _Attempt, method: Submit, kind: Literal["start", "checkpoint", "stop"]
    ) -> None:
        # Ключ повтора: потерянный ответ или перезапуск агента не создадут вторую операцию
        suffix = f".{a.pending_progress}" if kind == "checkpoint" else ""
        request_id = f"{key}.{kind}{suffix}"
        a.operation, _ = self.client.submit(method, a.container_id, request_id)
        a.pending = kind
        self._save()

    def _registration(self, a: _Attempt) -> Registration:
        return Registration(
            a.container_id, a.runtime_container, a.pair, cold_start=ColdStart(a.image, a.argv, a.env)
        )

    def _save(self) -> None:
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps({k: asdict(a) for k, a in self.attempts.items()}), encoding="utf-8")
        tmp.replace(self.file)
