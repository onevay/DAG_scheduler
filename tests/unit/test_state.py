from expsched.domain.resources import Resources
from expsched.domain.snapshot import RunInfo
from expsched.domain.states import TaskStatus
from expsched.engine.snapshot import build_snapshot
from expsched.engine.state import SimState


def test_initial_state_of_the_demo(demo_problem):
    s = SimState.from_problem(demo_problem)
    assert set(s.status.values()) == {TaskStatus.PENDING}
    assert s.rem == {k: t.duration for k, t in demo_problem.tasks.items()}
    assert set(s.attempt.values()) == {0}
    assert s.free == {
        "node-A": Resources({"gpu": 4}),
        "node-B": Resources({"gpu": 4}),
        "node-C": Resources({"gpu": 2}),
    }
    assert all(s.up.values()) and s.run == {} and s.ready_since == {}
    assert s.eligible["pretrain_vit"] == ("node-A", "node-B")
    assert s.waiting_preds["prep_data"] == 0 and s.waiting_preds["infer_bench"] == 2
    assert s.successors["prep_data"] == ["pretrain_vit", "train_asr"]


def test_duplicate_dependency_is_counted_once(make_task, make_problem):
    s = SimState.from_problem(make_problem([make_task("a"), make_task("b", 1, "a", "a")]))
    assert s.waiting_preds["b"] == 1 and s.successors["a"] == ["b"]


def test_unknown_dependency_keeps_the_task_waiting(make_task, make_problem):
    s = SimState.from_problem(make_problem([make_task("a", 1, "ghost")]))
    assert s.waiting_preds["a"] == 1


def test_add_task_registers_a_late_task(demo_problem, make_task):
    s = SimState.from_problem(demo_problem)
    s.add_task(make_task("late", 2, "eval_asr", gpu=2))
    assert s.status["late"] is TaskStatus.PENDING and s.waiting_preds["late"] == 1
    assert s.successors["eval_asr"] == ["urgent_eval", "late"]
    assert s.eligible["late"] == ("node-A", "node-B", "node-C")


def test_snapshot_reflects_state_and_sorts_ready(demo_problem):
    s = SimState.from_problem(demo_problem)
    s.status["train_asr"] = s.status["prep_data"] = TaskStatus.READY
    s.run["pretrain_vit"] = RunInfo("node-A", 2, 1, 10)
    snap = build_snapshot(s, 2)
    assert snap.t == 2 and snap.ready == ("prep_data", "train_asr")
    assert snap.running == {"pretrain_vit": RunInfo("node-A", 2, 1, 10)}
    assert snap.eligible == s.eligible and snap.up == s.up


def test_snapshot_is_detached_from_the_state(demo_problem):
    s = SimState.from_problem(demo_problem)
    snap = build_snapshot(s, 0)
    snap.free["node-A"] = Resources()
    snap.rem["prep_data"] = 0
    assert s.free["node-A"] == Resources({"gpu": 4}) and s.rem["prep_data"] == 2


def test_snapshot_analysis_uses_remaining_work_and_skips_finished_tasks(demo_problem):
    s = SimState.from_problem(demo_problem)
    s.status["prep_data"] = TaskStatus.SUCCEEDED
    s.rem["prep_data"] = 0
    s.rem["pretrain_vit"] = 6
    a = build_snapshot(s, 5).analysis
    assert "prep_data" not in a.order
    assert a.est["pretrain_vit"] == 5 and a.eft["pretrain_vit"] == 11
