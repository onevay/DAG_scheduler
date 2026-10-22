import json
from pathlib import Path

from expsched_ui import results

from expsched.contract.io_models import PolicyIn, load
from expsched.pipeline import run

SCENARIOS = Path(__file__).resolve().parents[2] / "scenarios"
DEMO_PATH = SCENARIOS / "demo.json"
DEMO = json.loads(DEMO_PATH.read_text(encoding="utf-8"))
RESULT = run(load(DEMO_PATH.read_bytes())).result
FAILURE_PATH = SCENARIOS / "failure.json"
FAILURE = json.loads(FAILURE_PATH.read_text(encoding="utf-8"))
FAILURE_RUN = run(load(FAILURE_PATH.read_bytes()))


def test_gantt_bars_cover_every_attempt_and_keep_concurrent_tasks_apart():
    bars = results.gantt_bars(RESULT, DEMO)
    assert len(bars) == len(RESULT["attempts"])
    by_lane: dict[str, list[tuple[int, int]]] = {}
    for b in bars:
        by_lane.setdefault(b["lane"], []).append((b["start"], b["end"]))
    for intervals in by_lane.values():  # на одной дорожке полосы не перекрываются
        intervals.sort()
        assert all(
            a_end <= b_start for (_, a_end), (b_start, _) in zip(intervals, intervals[1:], strict=False)
        )
    urgent = next(b for b in bars if b["task"] == "urgent_eval")
    assert urgent["submitted"] and not urgent["late"] and urgent["kind"] == "eval"


def test_lanes_are_reused_and_ordered_by_number():
    bars = [
        {
            "task": f"t{i}",
            "worker": "n",
            "start": 0,
            "end": 1,
            "kind": "other",
            "submitted": False,
            "late": False,
        }
        for i in range(11)
    ]
    lanes = [b["lane"] for b in results._assign_lanes(bars)]
    assert lanes[:3] == ["n · 1", "n · 2", "n · 3"] and lanes[-1] == "n · 11"


def test_interrupted_attempt_shows_where_the_work_was_lost():
    bars = [b for b in results.gantt_bars(FAILURE_RUN.result, FAILURE) if b["task"] == "pretrain_vit"]
    assert [(b["attempt"], b["worker"], b["start"], b["lost_from"], b["end"]) for b in bars] == [
        (1, "node-A", 2, 4, 5),  # 2 ч сохранены чекпоинтом, час 4–5 потерян
        (2, "node-B", 5, None, 11),
    ]


def test_outages_come_from_the_journal():
    assert results.outages(FAILURE_RUN.events, 22) == [{"worker": "node-A", "from": 5, "to": 15}]
    down = [{"type": "WORKER_DOWN", "t": 7, "worker": "n"}]
    assert results.outages(down, 20) == [{"worker": "n", "from": 7, "to": 20}]  # не вернулся до конца


def test_every_worker_has_a_lane_even_when_idle():
    bars = [{"worker": "b", "lane_index": 1}, {"worker": "b", "lane_index": 0}]
    assert results.lane_order(bars, ["c", "a", "b"]) == ["a · 1", "b · 1", "b · 2", "c · 1"]


def test_task_rows_count_attempts():
    rows = {r["task"]: r for r in results.task_rows(FAILURE_RUN.result)}
    assert rows["pretrain_vit"]["attempts"] == 2 and rows["ft_det"]["attempts"] == 1


def test_task_rows_sum_waits_and_name_the_main_reason():
    result = {
        "attempts": [],
        "tasks": {
            "b": {
                "status": "SUCCEEDED",
                "start": 5,
                "finish": 6,
                "deadline": None,
                "tardiness": None,
                "waits": [
                    {"code": "WAIT_DEPS", "from": 0, "to": 1, "detail": {}},
                    {"code": "WAIT_RESOURCES", "from": 1, "to": 5, "detail": {}},
                ],
            }
        },
    }
    (row,) = results.task_rows(result)
    assert (row["waited"], row["main_reason"]) == (5, "WAIT_RESOURCES")
    assert results.wait_rows(result, "b")[1] == {
        "from": 1,
        "to": 5,
        "duration": 4,
        "reason": "WAIT_RESOURCES",
        "detail": "{}",
    }


def test_comparison_has_a_row_per_policy():
    runs = {
        name: run(load(DEMO_PATH.read_bytes()), PolicyIn(name=name)).result for name in ("fifo", "priority")
    }
    rows = results.comparison_rows(runs)
    assert [r["policy"] for r in rows] == ["fifo", "priority"]
    assert all(r["makespan"] == 21 and r["violations"] == 0 and r["lost_work_gpu"] == 0 for r in rows)


def test_events_are_one_json_per_line():
    text = results.events_jsonl([{"seq": 0, "type": "READY"}, {"seq": 1, "type": "START"}])
    assert [json.loads(line)["seq"] for line in text.splitlines()] == [0, 1]
