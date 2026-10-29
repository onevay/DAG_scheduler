"""Сквозной прогон на агенте: сервер и агент — отдельными процессами, как на машине пользователя.

Пример `criu_pairs` (по умолчанию) идёт в режиме emulate; когда у какой-то попытки сохранён чекпоинт, её узел
роняется и через несколько секунд возвращается. Проверяется: прогон закончен, результат OK без нарушений,
а повтор прерванной задачи стартовал с сохранённой работы (`resume_from > 0`), а не с нуля.

    uv run python scripts/agent_smoke.py --runtime docker          # нужен образ: make workload-image
    uv run python scripts/agent_smoke.py --runtime gravesentinel   # Linux, гипервизор на --socket
    uv run python scripts/agent_smoke.py --runtime fake            # без контейнеров
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runtime", choices=["docker", "podman", "gravesentinel", "fake"], default="docker")
    parser.add_argument("--scenario", default="criu_pairs")
    parser.add_argument("--scale-ms", type=int, default=500, help="мс стены на единицу времени модели")
    parser.add_argument("--port", type=int, default=8031)
    parser.add_argument("--socket", help="сокет гипервизора gravesentinel")
    parser.add_argument("--no-drop", action="store_true", help="без отказа узла")
    parser.add_argument("--timeout", type=float, default=600, help="предел прогона, с")
    args = parser.parse_args()

    url = f"http://127.0.0.1:{args.port}"
    token = secrets.token_hex(16)
    work = Path(tempfile.mkdtemp(prefix="expsched-smoke-"))
    env = {**os.environ, "EXPSCHED_AGENT_TOKEN": token, "EXPSCHED_RUNS_DIR": str(work / "runs")}
    server_cmd = [sys.executable, "-m", "uvicorn", "expsched.api.app:app", "--port", str(args.port)]
    agent_cmd = [sys.executable, "-m", "expsched.agent", "--scheduler", url, "--id", "smoke"]
    agent_cmd += ["--runtime", args.runtime, "--state", str(work / "agent")]
    if args.socket:
        agent_cmd += ["--socket", args.socket]
    if args.runtime != "gravesentinel":  # четыре узла по CPU: машине CI с 4 ядрами хватит
        agent_cmd += ["--cpu", "4", "--ram-mb", "4096"]

    logs = {name: open(work / f"{name}.log", "w", encoding="utf-8") for name in ("server", "agent")}  # noqa: SIM115
    server = subprocess.Popen(server_cmd, cwd=ROOT, env=env, stdout=logs["server"], stderr=subprocess.STDOUT)
    agent: subprocess.Popen[bytes] | None = None
    try:
        wait_for(lambda: call(url, "GET", "/v1/policies"), 30, "server did not start")
        agent = subprocess.Popen(agent_cmd, cwd=ROOT, env=env, stdout=logs["agent"], stderr=subprocess.STDOUT)
        problem = json.loads((ROOT / "scenarios" / f"{args.scenario}.json").read_text(encoding="utf-8"))
        problem.update(mode="emulate", time_scale_ms=args.scale_ms)
        for worker in problem["cluster"]["workers"]:
            worker["agent"] = "smoke"
        run_id = call(url, "POST", "/v1/runs", {"problem": problem})["run_id"]
        print(f"run {run_id} on {args.runtime}, {args.scale_ms} ms per unit; logs in {work}")
        dropped = drive(url, run_id, args.timeout, drop=not args.no_drop)
        return check(url, run_id, dropped)
    except Exception as error:
        print(f"FAILED: {error}")
        for name, log in logs.items():
            log.flush()
            tail = (work / f"{name}.log").read_text(encoding="utf-8", errors="replace").splitlines()[-30:]
            print(f"--- {name} log tail ---", *tail, sep="\n")
        return 1
    finally:
        for process in (agent, server):
            if process is not None:
                process.terminate()
                process.wait(timeout=30)
        for log in logs.values():
            log.close()


def drive(url: str, run_id: str, timeout: float, drop: bool) -> str | None:
    """Ждать конца прогона, печатая смены состояний; уронить узел первой попытки с чекпоинтом и вернуть его.
    Ответ — задача, чей узел роняли."""
    seen: dict[str, str] = {}
    dropped: tuple[str, str] | None = None  # задача, её узел
    down_since: float | None = None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        live = call(url, "GET", f"/v1/runs/{run_id}/containers")
        for c in live["containers"]:
            key = f"{c['task']}#{c['attempt']}"
            line = f"{c['state']} progress {c['progress']}" + (f" ({c['detail']})" if c["detail"] else "")
            if seen.get(key) != line:
                print(f"  t={live['now']:>4} {key:<14} on {c['worker']}: {line}")
                seen[key] = line
        if live["status"] != "running":
            if live["status"] != "done":
                raise RuntimeError(f"run ended as {live['status']}")
            return dropped[0] if dropped else None
        saved = [c for c in live["containers"] if c["state"] == "checkpoint" and c["progress"] > 0]
        if drop and dropped is None and saved:
            dropped, down_since = (saved[0]["task"], saved[0]["worker"]), time.monotonic()
            print(f"  dropping {dropped[1]}: {dropped[0]} has {saved[0]['progress']} units saved")
            call(url, "POST", f"/v1/runs/{run_id}/actions", {"kind": "worker_down", "worker": dropped[1]})
        if dropped and down_since is not None and time.monotonic() - down_since > 3:
            print(f"  bringing {dropped[1]} back")
            call(url, "POST", f"/v1/runs/{run_id}/actions", {"kind": "worker_up", "worker": dropped[1]})
            down_since = None
        time.sleep(0.5)
    raise TimeoutError(f"run {run_id} did not finish in {timeout} s")


def check(url: str, run_id: str, dropped: str | None) -> int:
    result = call(url, "GET", f"/v1/runs/{run_id}")["result"]
    events = call(url, "GET", f"/v1/runs/{run_id}/events?from_seq=0&limit=10000")["events"]
    assert result["status"] == "OK", f"result status {result['status']}"
    assert result["violations"] == [], result["violations"]
    print(f"OK: makespan {result['metrics']['makespan']}, {len(result['attempts'])} attempts")
    if dropped:
        task = dropped
        resumed = [e for e in events if e["type"] == "START" and e["task"] == task and e["attempt"] > 1]
        assert resumed, f"{task} was not restarted after its node went down"
        assert resumed[0]["resume_from"] > 0, f"{task} restarted from zero, its checkpoint was lost"
        print(f"OK: {task} resumed from {resumed[0]['resume_from']} units on {resumed[0]['worker']}")
    return 0


def call(url: str, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    request = urllib.request.Request(
        url + path,
        data=None if body is None else json.dumps(body).encode(),
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def wait_for(probe: Any, seconds: float, message: str) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            probe()
            return
        except OSError:
            time.sleep(0.3)
    raise TimeoutError(message)


if __name__ == "__main__":
    sys.exit(main())
