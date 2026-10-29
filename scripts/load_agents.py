"""Нагрузка на планировщик прогонов на агентах: много агентов fake и большой DAG, в одном процессе.

Агенты, реестр и драйвер — настоящие, связь — прямыми вызовами (без HTTP), часы — общие, шаг — `--step-ms`
стены. Меряется то, что в сервере идёт в цикле событий: тик драйвера (решения политики, команды, факты),
опрос агента и приём его событий; отдельно — подготовка и итог прогона (в сервере они идут в потоке), пик
памяти и результат.

    uv run python scripts/load_agents.py                            # 20 агентов, 500 задач, heft и backfill
    uv run python scripts/load_agents.py --agents 5 --tasks 100 --policy heft
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path
from typing import Any

from expsched.agent.core import Agent
from expsched.agent.runtime import FakeRuntime
from expsched.contract.io_models import ProblemIn
from expsched.emulate.agents import AgentRegistry
from expsched.emulate.driver import Emulation

CAPACITY = {"cpu": 16, "ram_mb": 65536, "gpu": 2}


def problem(agents: int, tasks: int, seed: int) -> ProblemIn:
    """Слои по 25 задач, у каждой — до трёх зависимостей из прошлого слоя; узлы разной скорости."""
    rng = random.Random(seed)
    workers = [
        {
            "id": f"w{i:02d}",
            "resources": CAPACITY,
            "speed": rng.choice([1.0, 1.0, 1.5, 2.0]),
            "agent": f"a{i:02d}",
        }
        for i in range(agents)
    ]
    out: list[dict[str, Any]] = []
    for i in range(tasks):
        layer = i // 25
        before = [t["id"] for t in out if int(t["id"][1:]) // 25 == layer - 1]
        gpu = rng.random() < 0.3
        out.append(
            {
                "id": f"t{i:04d}",
                "kind": "train" if gpu else "data",
                "duration": rng.randint(3, 30),
                "resources": {"gpu": 1, "cpu": 4, "ram_mb": 8192} if gpu else {"cpu": 2, "ram_mb": 4096},
                "dependencies": rng.sample(before, min(len(before), rng.randint(0, 3))),
                "checkpoint_interval": 5,
                "priority": rng.randint(0, 5),
            }
        )
    return ProblemIn.model_validate(
        {
            "name": f"load-{agents}x{tasks}",
            "mode": "emulate",
            "time_scale_ms": 50,
            "cluster": {"workers": workers},
            "tasks": out,
            "seed": seed,
        }
    )


def ms(samples: list[float]) -> dict[str, float]:
    if not samples:
        return {}
    ordered = sorted(samples)
    return {
        "mean": round(statistics.fmean(ordered) * 1000, 3),
        "p95": round(ordered[int(0.95 * (len(ordered) - 1))] * 1000, 3),
        "max": round(ordered[-1] * 1000, 3),
    }


def run(p: ProblemIn, policy: str, step_ms: int, root: Path) -> dict[str, Any]:
    now = [0.0]
    registry = AgentRegistry(clock=lambda: now[0], heartbeat_s=60)
    agents = {}
    for w in p.cluster.workers:
        runtime = FakeRuntime()
        agent = Agent(str(w.agent), runtime, root / str(w.agent), CAPACITY, max_containers=8, now=lambda: "T")
        agent.welcome(registry.hello(str(w.agent), agent.hello()))
        agents[str(w.agent)] = (agent, runtime)
    tracemalloc.start()
    started = time.perf_counter()
    emulation = Emulation(
        "0123456789abcdef", p, p.policy.model_copy(update={"name": policy}), registry, clock=lambda: now[0]
    )
    prepare_s = time.perf_counter() - started
    ticks: list[float] = []
    polls: list[float] = []
    events: list[float] = []
    steps, finish_s = 0, 0.0
    while emulation.status == "running" and steps < 200_000:
        t0 = time.perf_counter()
        emulation.tick(conclude=False)  # как в сервере: итог — отдельно, в потоке
        ticks.append(time.perf_counter() - t0)
        if emulation.concluding:
            t0 = time.perf_counter()
            emulation.finish()
            finish_s = time.perf_counter() - t0
            break
        for name, (agent, runtime) in agents.items():
            t0 = time.perf_counter()
            agent.handle(registry.poll(name, agent.poll(0)))
            polls.append(time.perf_counter() - t0)
            runtime.advance(step_ms)
            agent.watch()
            if batch := agent.batch():
                t0 = time.perf_counter()
                agent.acked(registry.events(name, batch))
                events.append(time.perf_counter() - t0)
        now[0] += step_ms / 1000
        steps += 1
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    result = emulation.result.result if emulation.result else {}
    return {
        "policy": policy,
        "status": result.get("status", emulation.status),
        "violations": len(result.get("violations", [])),
        "makespan": result.get("metrics", {}).get("makespan"),
        "lower_bound": result.get("metrics", {}).get("lower_bound"),
        "steps": steps,
        "wall_s": round(time.perf_counter() - started, 2),
        "prepare_ms": round(prepare_s * 1000, 1),
        "finish_ms": round(finish_s * 1000, 1),
        "tick_ms": ms(ticks),
        "poll_ms": ms(polls),
        "events_ms": ms(events),
        "peak_mb": round(peak / 2**20, 1),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--agents", type=int, default=20)
    parser.add_argument("--tasks", type=int, default=500)
    parser.add_argument("--policy", action="append", help="по умолчанию heft и priority_backfill")
    parser.add_argument("--step-ms", type=int, default=50)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()
    p = problem(args.agents, args.tasks, args.seed)
    out = []
    for policy in args.policy or ["heft", "priority_backfill"]:
        with tempfile.TemporaryDirectory() as root:
            report = run(p, policy, args.step_ms, Path(root))
        out.append(report)
        print(json.dumps(report, ensure_ascii=False), flush=True)
    return 0 if all(r["status"] == "OK" and r["violations"] == 0 for r in out) else 1


if __name__ == "__main__":
    sys.exit(main())
