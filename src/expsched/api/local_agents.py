"""Агенты на машине сервера, запущенные из интерфейса: сервер сам держит их процессы.

Каждый — обычный `python -m expsched.agent` со своим каталогом состояния и журналом процесса; токен агентов
сервера (заданный или придуманный при запуске) передаётся ему через окружение. Перед запуском —
`agent check`: не готова среда (нет Docker, нет образа нагрузки) или нет связи — агент не запускается,
а вывод проверки возвращается как есть.
gravesentinel здесь не запускается: его агенту нужен пользователь гипервизора, а сервер работает от обычного.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Runtime = Literal["docker", "podman", "fake"]
Check = Callable[[Sequence[str], Mapping[str, str]], tuple[int, str]]
Spawn = Callable[[Sequence[str], Mapping[str, str], Path], "subprocess.Popen[bytes]"]
LIMIT = int(os.environ.get("EXPSCHED_LOCAL_AGENTS", "8"))
LOG_TAIL = 40  # строк журнала процесса в ответе


class CheckFailed(Exception):
    """Проверка агента не прошла: в сообщении — её вывод."""


def run_check(args: Sequence[str], env: Mapping[str, str]) -> tuple[int, str]:
    done = subprocess.run(list(args), env=dict(env), capture_output=True, text=True, timeout=60, check=False)
    return done.returncode, (done.stdout + done.stderr).strip()


def spawn(args: Sequence[str], env: Mapping[str, str], log: Path) -> subprocess.Popen[bytes]:
    with log.open("ab") as out:  # дескриптор у процесса свой: файл можно закрыть здесь
        return subprocess.Popen(list(args), env=dict(env), stdout=out, stderr=subprocess.STDOUT)


@dataclass
class LocalAgent:
    id: str
    runtime: Runtime
    process: subprocess.Popen[bytes]
    log: Path
    started: float


class LocalAgents:
    def __init__(
        self, root: Path, check: Check = run_check, start: Spawn = spawn, limit: int = LIMIT
    ) -> None:
        self.root = root
        self.check, self.spawn, self.limit = check, start, limit
        self.agents: dict[str, LocalAgent] = {}
        self.starting: set[str] = set()  # идёт проверка: место уже занято, второй запуск того же id — отказ
        self.lock = threading.Lock()

    def start(
        self, agent_id: str, runtime: Runtime, capacity: Mapping[str, int], scheduler: str, token: str = ""
    ) -> LocalAgent:
        """Проверить и запустить. ValueError — такой уже идёт или их слишком много; CheckFailed — среда
        или связь не готовы."""
        with self.lock:
            current = self.agents.get(agent_id)
            if agent_id in self.starting or (current is not None and current.process.poll() is None):
                raise ValueError(f"local agent {agent_id} is already running")
            if sum(a.process.poll() is None for a in self.agents.values()) + len(self.starting) >= self.limit:
                raise ValueError(f"{self.limit} local agents are already running (EXPSCHED_LOCAL_AGENTS)")
            self.starting.add(agent_id)
        try:
            return self._start(agent_id, runtime, capacity, scheduler, token)
        finally:
            with self.lock:
                self.starting.discard(agent_id)

    def _start(
        self, agent_id: str, runtime: Runtime, capacity: Mapping[str, int], scheduler: str, token: str
    ) -> LocalAgent:
        state = self.root / agent_id
        state.mkdir(parents=True, exist_ok=True)
        args = [
            "--id",
            agent_id,
            "--runtime",
            runtime,
            "--scheduler",
            scheduler,
            "--state",
            str(state / "state"),
        ]
        for name, flag in (("cpu", "--cpu"), ("ram_mb", "--ram-mb"), ("gpu", "--gpu")):
            if capacity.get(name) is not None:
                args += [flag, str(capacity[name])]
        env = {**os.environ, **({"EXPSCHED_AGENT_TOKEN": token} if token else {})}
        code, output = self.check([sys.executable, "-m", "expsched.agent", "check", "--no-hello", *args], env)
        if code != 0:
            raise CheckFailed(output[-2000:] or f"agent check exited with {code}")
        log = state / "agent.log"
        process = self.spawn([sys.executable, "-m", "expsched.agent", *args], env, log)
        with self.lock:
            self.agents[agent_id] = LocalAgent(agent_id, runtime, process, log, time.time())
            return self.agents[agent_id]

    def stop(self, agent_id: str, timeout_s: float = 15) -> bool:
        """SIGTERM (на Windows — завершение процесса), ожидание, затем kill. False — такого агента нет."""
        with self.lock:
            agent = self.agents.pop(agent_id, None)
        if agent is None:
            return False
        if agent.process.poll() is None:
            agent.process.terminate()
            try:
                agent.process.wait(timeout_s)
            except subprocess.TimeoutExpired:
                agent.process.kill()
                agent.process.wait(timeout_s)
        return True

    def describe(self) -> list[dict[str, object]]:
        out = []
        for agent in sorted(self.agents.values(), key=lambda a: a.id):
            code = agent.process.poll()
            out.append(
                {
                    "id": agent.id,
                    "runtime": agent.runtime,
                    "pid": agent.process.pid,
                    "alive": code is None,
                    "exit_code": code,
                    "log": tail(agent.log),
                }
            )
        return out

    def close(self) -> None:
        for agent_id in list(self.agents):
            self.stop(agent_id, timeout_s=5)


def tail(path: Path, lines: int = LOG_TAIL) -> list[str]:
    try:
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 16 * 1024))
            return f.read().decode("utf-8", errors="replace").splitlines()[-lines:]
    except OSError:
        return []
