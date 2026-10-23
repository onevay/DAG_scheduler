import os
from concurrent.futures import ThreadPoolExecutor

from expsched.api.store import RunStore
from expsched.pipeline import RunResult


def outcome(n):
    return RunResult(
        {"input_hash": "sha256:" + "0" * 64, "n": n}, [{"seq": i, "t": i, "type": "READY"} for i in range(n)]
    )


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
