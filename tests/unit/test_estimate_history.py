"""Оценки по истории: наблюдения, подгонка закона Амдала, файл истории, проигрывание с прежней работой."""

import json

import pytest
from test_emulation import Loop, laptop

from expsched.api.history import LIMIT, History
from expsched.contract.io_models import ProblemIn, TaskIn
from expsched.emulate.replay import replay
from expsched.estimate import Estimate, resolve
from expsched.estimate.history import Observation, estimates, fit, observe, pinned


def problem(tasks, mode="emulate", scale=100, workers=None):
    return ProblemIn.model_validate(
        {
            "mode": mode,
            "time_scale_ms": scale,
            "cluster": {
                "workers": workers or [{"id": "w", "resources": {"gpu": 8}, "agent": "a", "speed": 2}]
            },
            "tasks": tasks,
        }
    )


def attempt(task, start, end, outcome="DONE", resume_from=0, worker="w"):
    return {"task": task, "attempt": 1, "worker": worker, "start": start, "end": end,
            "resume_from": resume_from, "saved": 0, "outcome": outcome}  # fmt: skip


def test_observed_work_counts_saved_work_and_the_speed_of_the_last_node():
    p = problem(
        [
            {"id": "train", "kind": "train", "resources": {"gpu": 4}},
            {
                "id": "own",
                "kind": "train",
                "container": {"image": "lab/train:3", "command": ["python", "t.py"]},
            },
        ]
    )
    result = {
        "attempts": [
            attempt("train", 0, 4, "INT"),
            attempt("train", 6, 16, resume_from=8),
            attempt("own", 0, 5),
        ]
    }
    seen = observe(p, result)
    assert seen == {
        "task:train:train": [Observation(8 + 10 * 2, 4, "model")],
        "task:own:train image:lab/train:3 python t.py": [
            Observation(5 * 2 * 100, 1, "ms")
        ],  # своя задача — в мс стены
    }


def test_fit_recovers_the_serial_fraction_from_runs_with_different_gpu_counts():
    truth = 100  # работа на одном GPU, доля последовательной части — 0,2
    on = {1: truth, 4: truth * (0.2 + 0.8 / 4)}
    seen = [Observation(on[n] * noise, n, "model") for n in (1, 4) for noise in (0.97, 1.03)]
    task = TaskIn(id="train", kind="train", resources={"gpu": 2})
    found = fit(seen, task, time_scale_ms=100)
    assert found is not None and found.source == "history"
    assert found.median == pytest.approx(truth * (0.2 + 0.8 / 2), rel=0.03)  # на 2 GPU — 0,6 от одного
    assert 0.05 <= found.spread < 0.1


def test_work_in_milliseconds_follows_the_time_scale_of_the_new_run():
    task = TaskIn(id="own", kind="eval")
    seen = [Observation(60_000, 1, "ms")]
    assert fit(seen, task, time_scale_ms=1000).median == pytest.approx(60)
    assert fit(seen, task, time_scale_ms=500).median == pytest.approx(120)
    assert fit([], task, time_scale_ms=1000) is None


def test_history_estimates_only_tasks_without_duration():
    p = problem([{"id": "a", "kind": "data"}, {"id": "b", "kind": "data", "duration": 5}])
    history = {"task:a:data": [Observation(30, 1, "model")], "task:b:data": [Observation(30, 1, "model")]}
    assert set(estimates(history, p)) == {"task:a:data"}


def test_history_file_keeps_the_latest_observations_and_skips_simulations(tmp_path):
    history = History(tmp_path / "estimates.json")
    emulated = problem([{"id": "a", "kind": "data"}]).model_dump(mode="json")
    for i in range(LIMIT + 3):
        history.record(emulated, {"attempts": [attempt("a", 0, 10 + i)]})
    kept = history.load()["task:a:data"]
    assert len(kept) == LIMIT and kept[-1].work == (10 + LIMIT + 2) * 2
    simulated = problem([{"id": "s", "kind": "data", "duration": 3}], mode="simulate").model_dump(mode="json")
    history.record(simulated, {"attempts": [attempt("s", 0, 3)]})
    assert "task:s:data" not in history.load()
    (tmp_path / "estimates.json").write_text("{broken", encoding="utf-8")
    assert history.load() == {}


def test_pinned_estimates_keep_the_work_and_spread_of_the_run():
    p = problem([{"id": "a", "estimate": 10}, {"id": "b", "duration": 4}])
    recorded = {
        "a": {"median": 10.0, "spread": 0.5, "mean": 11.331, "p80": 15.2, "work": 17, "source": "estimate"}
    }
    pins = pinned(p, recorded)
    assert pins == {"task:a:other": Estimate(10.0, 0.5, "estimate", 17)} and pinned(p, None) == {}
    _, found = resolve(p, pins)  # закреплённая сильнее оценки во входе
    assert found["a"].work() == 17 and found["a"].spread == 0.5


def test_second_run_learns_from_the_first_and_replay_keeps_the_old_work(tmp_path):
    tasks = [t.model_copy(update={"duration": None}) for t in laptop().tasks]
    first = laptop(tasks=tasks, mode="emulate")
    run = Loop(tmp_path / "1", first).run()
    assert {e.source for e in run.prepared.estimates.values()} == {"kind"}  # истории ещё нет
    history = History(tmp_path / "estimates.json")
    history.record(first.model_dump(mode="json"), run.result.result)

    learned = history.estimates(first)
    assert {e.source for e in learned.values()} == {"history"}
    second = Loop(tmp_path / "2", first)  # тот же вход: теперь оценки — по истории
    second.emulation = type(second.emulation)(
        "fedcba9876543210", first, first.policy, second.registry, clock=lambda: second.now, history=learned
    )
    assert {e.source for e in second.emulation.prepared.estimates.values()} == {"history"}

    # история изменилась, а проигрывание первого прогона — с той работой, что была у задач тогда
    stored = run.result.result
    same = replay(first, run.result.events, pinned(first, stored["estimates"]))
    assert same["same"] and same["same_journal"]
    json.dumps(stored["estimates"])  # результат сериализуется как есть
