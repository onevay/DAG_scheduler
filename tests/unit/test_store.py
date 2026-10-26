import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from expsched.api.store import RunNotFound, RunOutdated, RunStore
from expsched.pipeline import SCHEMA_VERSION, RunResult


def outcome(n):
    result = {
        "schema_version": SCHEMA_VERSION,
        "n": n,
        "policy": "fifo",
        "status": "OK",
        "tasks": {},
        "metrics": {"makespan": n},
    }
    return RunResult(result, [{"seq": i, "t": i, "type": "READY"} for i in range(n)])


def test_saved_run_is_read_back(tmp_path):
    store = RunStore(tmp_path)
    store.save("a" * 16, {"tasks": []}, outcome(5))
    assert store.find("a" * 16) is not None and store.result("a" * 16)["n"] == 5
    assert store.problem("a" * 16) == {"tasks": []}
    events, total = store.events("a" * 16, 3, 10)
    assert [r["seq"] for r in events] == [3, 4] and total == 5


def test_saved_run_is_not_overwritten(tmp_path):
    store = RunStore(tmp_path)
    store.save("a" * 16, {}, outcome(1))
    store.save("a" * 16, {}, outcome(2))
    assert store.result("a" * 16)["n"] == 1 and store.find("b" * 16) is None


def test_oldest_runs_are_evicted(tmp_path):
    store = RunStore(tmp_path, max_runs=2)
    for i, run_id in enumerate(["a" * 16, "b" * 16, "c" * 16]):
        store.save(run_id, {}, outcome(1))
        os.utime(tmp_path / run_id, (i, i))  # явный порядок: время создания на быстрой машине совпадает
    store.save("d" * 16, {}, outcome(1))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["c" * 16, "d" * 16]


def test_concurrent_saves_of_one_run_leave_one_copy(tmp_path):
    store = RunStore(tmp_path)
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda _: store.save("a" * 16, {}, outcome(3)), range(8)))
    assert store.result("a" * 16)["n"] == 3
    assert [p.name for p in tmp_path.iterdir()] == ["a" * 16]  # временных каталогов не осталось


def test_recent_runs_come_from_summaries(tmp_path):
    store = RunStore(tmp_path)
    store.save("a" * 16, {"name": "first"}, outcome(4))
    store.save("b" * 16, {}, outcome(7))
    recent = store.recent(10)
    assert [(r["run_id"], r["name"], r["makespan"]) for r in recent] == [
        ("b" * 16, None, 7),
        ("a" * 16, "first", 4),
    ]
    assert len(store.recent(1)) == 1 and RunStore(tmp_path / "none").recent(5) == []


def test_missing_run_is_reported_as_not_found(tmp_path):
    store = RunStore(tmp_path)
    for read in (store.result, store.problem, lambda run_id: store.events(run_id, 0, 10)):
        with pytest.raises(RunNotFound) as error:
            read("a" * 16)
        assert error.value.run_id == "a" * 16


def test_oldest_runs_are_evicted_when_they_take_too_much_space(tmp_path):
    one_run = sum(f.stat().st_size for f in _saved(RunStore(tmp_path / "probe"), "a" * 16).iterdir())
    store = RunStore(tmp_path / "runs", max_runs=100, max_bytes=2 * one_run + one_run // 2)
    for i, run_id in enumerate(["a" * 16, "b" * 16, "c" * 16]):
        _saved(store, run_id)
        os.utime(tmp_path / "runs" / run_id, (i, i))
    assert sorted(p.name for p in (tmp_path / "runs").iterdir()) == ["b" * 16, "c" * 16]


def test_the_run_just_saved_is_kept_even_if_alone_it_is_too_big(tmp_path):
    store = RunStore(tmp_path, max_bytes=1)
    _saved(store, "a" * 16)
    assert [p.name for p in tmp_path.iterdir()] == ["a" * 16]


def test_run_of_an_older_result_version_is_outdated(tmp_path):
    store = RunStore(tmp_path)
    store.save("a" * 16, {}, RunResult({**outcome(1).result, "schema_version": "1.0"}, []))
    store.save("b" * 16, {}, outcome(2))
    with pytest.raises(RunOutdated):
        store.result("a" * 16)
    assert [s["run_id"] for s in store.recent(10)] == ["b" * 16]
    store.discard("a" * 16)
    assert store.find("a" * 16) is None


def _saved(store, run_id):
    store.save(run_id, {}, outcome(50))
    return store.root / run_id
