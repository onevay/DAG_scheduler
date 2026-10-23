import dataclasses
from pathlib import Path

from expsched.contract.io_models import PolicyIn, load
from expsched.domain.models import Event, EvType
from expsched.engine.simulator import Simulator
from expsched.pipeline import run
from expsched.policies.priority import PriorityPolicy

SCENARIOS = Path(__file__).resolve().parents[2] / "scenarios"


def replans(name):
    result = run(load((SCENARIOS / f"{name}.json").read_bytes()), PolicyIn(name="priority")).result
    return [{k: v for k, v in r.items() if k not in ("seq", "type")} for r in result["replans"]]


def test_replan_diff_on_urgent_submit():
    # на node-C было свободное окно: срочная задача встаёт в план, никого не сдвигая
    assert replans("demo") == [
        {
            "t": 9,
            "trigger": "submit_task",
            "events": [{"type": "submit_task", "task": "urgent_eval"}],
            "changes": [],
            "new_tasks": {"urgent_eval": 10},
            "dropped": [],
        }
    ]


def test_forecast_after_failure_shifts_chain():
    down, up = replans("failure")
    shifts = {c["task"]: c["delta"] for c in down["changes"]}
    # потерян час работы pretrain_vit: вся цепочка за ней сдвигается ровно на 1
    assert shifts == {t: 1 for t in ("ft_det", "ft_seg", "distill", "eval_det", "eval_seg", "infer_bench")}
    assert down["new_tasks"] == {"pretrain_vit": 5}  # прерванная попытка продолжается на node-B
    assert (up["trigger"], up["changes"], up["new_tasks"]) == ("worker_up", [], {})


def test_tasks_that_will_not_start_are_dropped(make_task, make_problem):
    tasks = [make_task("a", 4, gpu=4, max_retries=0), make_task("b", 1, "a")]
    events = [Event(2, EvType.WORKER_DOWN, worker="n0"), Event(2, EvType.SUBMIT, task=make_task("c", 1))]
    log = Simulator(
        make_problem(tasks, gpus=(4, 4), events=events), PriorityPolicy(), forecast=True
    ).run_all()
    (replan,) = [r for r in log if r["type"] == "REPLAN"]
    assert replan["trigger"] == "worker_down"  # в один момент отказ обрабатывается раньше подачи
    assert replan["events"] == [{"type": "worker_down", "worker": "n0"}, {"type": "submit_task", "task": "c"}]
    assert replan["dropped"] == ["b"]  # a провалена, b заблокирована
    assert replan["new_tasks"] == {"c": 2}  # стартовала в эту же эпоху на n1


def without_seq(log):
    return [{k: v for k, v in r.items() if k != "seq"} for r in log if r["type"] != "REPLAN"]


def test_forecast_does_not_change_the_run(random_problem, demo_problem):
    for problem in [demo_problem] + [random_problem(seed, failures=True) for seed in range(10)]:
        plain = Simulator(problem, PriorityPolicy()).run_all()
        with_replans = Simulator(problem, PriorityPolicy(), forecast=True).run_all()
        assert without_seq(with_replans) == without_seq(plain)


def test_replans_are_off_by_default(demo_problem):
    submitted = dataclasses.replace(
        demo_problem,
        tasks={k: t for k, t in demo_problem.tasks.items() if k != "urgent_eval"},
        events=[Event(9, EvType.SUBMIT, task=demo_problem.tasks["urgent_eval"])],
    )
    assert not any(r["type"] == "REPLAN" for r in Simulator(submitted, PriorityPolicy()).run_all())


def test_infeasible_result_has_no_replans():
    problem = load((SCENARIOS / "cycle.json").read_bytes())
    result = run(problem, problem.policy.model_copy(update={"validation_mode": "strict"})).result
    assert result["status"] == "INFEASIBLE" and result["replans"] == []


def test_large_inputs_are_not_forecast():
    problem = load((SCENARIOS / "demo.json").read_bytes())  # 11 задач с поданной
    for limit, built in [(11, True), (10, False), (0, False)]:
        result = run(problem, problem.policy.model_copy(update={"max_forecast_tasks": limit})).result
        assert (result["replans"] is not None) == built
