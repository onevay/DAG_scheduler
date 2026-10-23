"""Прогоны в отдельных процессах: долгий вход не держит сервер, а зависший прогон снимается по таймауту.

Рабочие процессы переиспользуются: запуск процесса стоит сотни миллисекунд, а прогон небольшого входа —
единицы. Процесс, не уложившийся в таймаут, убивается, и следующий прогон получает новый. Одновременно идёт
не больше `workers` прогонов, остальные ждут своей очереди.
"""

from __future__ import annotations

import asyncio
import multiprocessing
import queue
import threading
import traceback
from dataclasses import dataclass
from multiprocessing.process import BaseProcess
from typing import Any, Protocol

from expsched.contract.io_models import PolicyIn, ProblemIn
from expsched.pipeline import RunResult, run


class RunTimeout(RuntimeError):
    """Прогон не закончился за отведённое время; его процесс убит."""


class RunFailed(RuntimeError):
    """Прогон упал с ошибкой; в сообщении — трассировка из рабочего процесса."""


class _Pipe(Protocol):
    """Конец канала между процессами: Connection в Linux, PipeConnection в Windows."""

    def send(self, obj: Any) -> None: ...
    def recv(self) -> Any: ...
    def poll(self, timeout: float | None = ...) -> bool: ...
    def close(self) -> None: ...


def _serve(conn: _Pipe) -> None:
    """Цикл рабочего процесса: принимает вход и политику в JSON, отвечает (статус, результат или ошибка)."""
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
    def __init__(self, timeout: float = 60, workers: int = 2) -> None:
        self.timeout = timeout
        self._slots = threading.BoundedSemaphore(workers)
        self._idle: queue.SimpleQueue[_Worker] = queue.SimpleQueue()
        self._context = multiprocessing.get_context("spawn")  # одинаково на Linux и Windows

    async def run(self, problem: ProblemIn, policy: PolicyIn) -> RunResult:
        """Прогон в рабочем процессе. NotImplementedError — политика не реализована; RunTimeout, RunFailed.

        Если свободного процесса нет, в таймаут входит и запуск нового (около полсекунды).
        """
        return await asyncio.to_thread(self._run, problem.model_dump_json(), policy.model_dump_json())

    def close(self) -> None:
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
        process = self._context.Process(target=_serve, args=(child,), daemon=True)
        process.start()
        child.close()
        return _Worker(process, parent)

    @staticmethod
    def _stop(worker: _Worker) -> None:
        worker.conn.close()
        if worker.process.is_alive():
            worker.process.kill()
        worker.process.join(timeout=5)
