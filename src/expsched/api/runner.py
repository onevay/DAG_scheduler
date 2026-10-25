"""Прогоны в отдельных процессах: долгий вход не держит сервер, а зависший прогон снимается по таймауту.

Рабочие процессы переиспользуются: запуск процесса стоит сотни миллисекунд, а прогон небольшого входа —
единицы. Процесс, не уложившийся в таймаут, убивается, и следующий прогон получает новый. Одновременно идёт
не больше `workers` прогонов, остальные ждут своей очереди; всего — идущих и ждущих — не больше `queue_size`,
сверх этого прогон не принимается (RunQueueFull), чтобы запросы не копились без предела. Ожидание идёт в своих
потоках, а не в общем пуле asyncio: остальной работе сервера потоки остаются. Адресное пространство рабочего
процесса ограничено `memory_mb` там, где ОС это умеет (Linux, macOS); на Windows предела нет.
"""

from __future__ import annotations

import asyncio
import multiprocessing
import queue
import sys
import threading
import traceback
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from multiprocessing.process import BaseProcess
from typing import Any, Protocol

from expsched.contract.io_models import PolicyIn, ProblemIn
from expsched.pipeline import RunResult, run


class RunTimeout(RuntimeError):
    """Прогон не закончился за отведённое время; его процесс убит."""


class RunFailed(RuntimeError):
    """Прогон упал с ошибкой; в сообщении — трассировка из рабочего процесса."""


class RunQueueFull(RuntimeError):
    """Прогонов, идущих и ждущих, уже столько, сколько позволено; новый не принят."""


class _Pipe(Protocol):
    """Конец канала между процессами: Connection в Linux, PipeConnection в Windows."""

    def send(self, obj: Any) -> None: ...
    def recv(self) -> Any: ...
    def poll(self, timeout: float | None = ...) -> bool: ...
    def close(self) -> None: ...


def _limit_memory(memory_mb: int | None) -> bool:
    """Предел адресного пространства процесса; False — предела нет (не задан или ОС не умеет)."""
    if not memory_mb or sys.platform == "win32":
        return False
    import resource

    limit = memory_mb << 20
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    return True


def _serve(conn: _Pipe, memory_mb: int | None = None) -> None:
    """Цикл рабочего процесса: принимает вход и политику в JSON, отвечает (статус, результат или ошибка)."""
    _limit_memory(memory_mb)
    while True:
        try:
            problem, policy = conn.recv()
        except EOFError:
            return
        try:
            conn.send(
                ("ok", run(ProblemIn.model_validate_json(problem), PolicyIn.model_validate_json(policy)))
            )
        except NotImplementedError as error:
            conn.send(("not_implemented", str(error)))
        except Exception:
            conn.send(("failed", traceback.format_exc()))


@dataclass
class _Worker:
    process: BaseProcess
    conn: _Pipe


class ProcessRunner:
    def __init__(
        self, timeout: float = 60, workers: int = 2, queue_size: int = 32, memory_mb: int | None = None
    ) -> None:
        self.timeout = timeout
        self.capacity = max(queue_size, workers)
        self.memory_mb = memory_mb
        self._slots = threading.BoundedSemaphore(workers)
        self._idle: queue.SimpleQueue[_Worker] = queue.SimpleQueue()
        self._context = multiprocessing.get_context("spawn")  # одинаково на Linux и Windows
        self._pending = 0
        self._lock = threading.Lock()
        self._threads = ThreadPoolExecutor(self.capacity, thread_name_prefix="expsched-run")

    @contextmanager
    def reserve(self, count: int = 1) -> Iterator[None]:
        """Места в очереди под `count` прогонов — все сразу или ни одного (RunQueueFull)."""
        with self._lock:
            if self._pending + count > self.capacity:
                raise RunQueueFull(
                    f"{self._pending} runs are queued, {count} more exceed the limit of {self.capacity}"
                )
            self._pending += count
        try:
            yield
        finally:
            with self._lock:
                self._pending -= count

    async def run(self, problem: ProblemIn, policy: PolicyIn) -> RunResult:
        """Прогон в рабочем процессе. NotImplementedError — политика не реализована; RunTimeout, RunFailed.

        Место в очереди берёт вызывающий (`reserve`). Если свободного процесса нет, в таймаут входит и запуск
        нового (около полсекунды).
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._threads, self._run, problem.model_dump_json(), policy.model_dump_json()
        )

    def close(self) -> None:
        self._threads.shutdown(wait=False, cancel_futures=True)
        while True:
            try:
                self._stop(self._idle.get_nowait())
            except queue.Empty:
                return

    def _run(self, problem: str, policy: str) -> RunResult:
        with self._slots:
            worker = self._take()
            worker.conn.send((problem, policy))
            if not worker.conn.poll(self.timeout):
                self._stop(worker)
                raise RunTimeout(f"run did not finish in {self.timeout:g} s")
            try:
                status, payload = worker.conn.recv()
            except EOFError:
                self._stop(worker)
                raise RunFailed("worker process exited during the run") from None
            self._idle.put(worker)
        if status == "not_implemented":
            raise NotImplementedError(payload)
        if status == "failed":
            raise RunFailed(payload)
        result: RunResult = payload
        return result

    def _take(self) -> _Worker:
        while True:
            try:
                worker = self._idle.get_nowait()
            except queue.Empty:
                break
            if worker.process.is_alive():
                return worker
            self._stop(worker)
        parent, child = self._context.Pipe()
        process = self._context.Process(target=_serve, args=(child, self.memory_mb), daemon=True)
        process.start()
        child.close()
        return _Worker(process, parent)

    @staticmethod
    def _stop(worker: _Worker) -> None:
        worker.conn.close()
        if worker.process.is_alive():
            worker.process.kill()
        worker.process.join(timeout=5)
