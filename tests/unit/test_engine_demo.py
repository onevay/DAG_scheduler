"""Прогон демо-DAG целиком: корректность расписания и детерминизм журнала."""

import dataclasses

from expsched.analysis import analyze
from expsched.domain.models import Event, EvType, Problem
from expsched.domain.snapshot import Decision
from expsched.domain.states import TaskStatus
from expsched.engine.simulator import Simulator


def online(demo: Problem) -> Problem:
    """Тот же DAG, но urgent_eval подаётся событием в t = 9."""
    urgent = demo.tasks["urgent_eval"]
    tasks = {k: t for k, t in demo.tasks.items() if k != "urgent_eval"}
    return dataclasses.replace(demo, tasks=tasks, events=[Event(9, EvType.SUBMIT, task=urgent)])


def reordered(problem: Problem) -> Problem:
    """Тот же вход с обратным порядком задач и узлов."""
    return dataclasses.replace(
        problem,
        tasks=dict(reversed(problem.tasks.items())),
        workers=dict(reversed(problem.workers.items())),
    )


class FirstFreeWorker:
    """Берёт первый подходящий узел в порядке обхода `snapshot.free`: чувствительна к порядку словаря."""

    name = "first-free"

    def decide(self, snapshot):
        free, out = dict(snapshot.free), []
        for key in snapshot.ready:
            need = snapshot.tasks[key].resources
            worker = next((w for w in free if w in snapshot.eligible[key] and need.fits(free[w])), None)
            if worker is not None:
                free[worker] = free[worker] - need
                out.append(Decision("START", key, worker, {"code": "FIRST_FREE"}))
        return out


def test_demo_runs_to_completion_with_a_valid_schedule(demo_problem, greedy, check_capacity):
    sim = Simulator(demo_problem, greedy)
    log = sim.run_all()
    assert set(sim.state.status.values()) == {TaskStatus.SUCCEEDED}
    check_capacity(demo_problem, log)
    analysis = analyze(demo_problem)
    starts = {r["task"]: r["t"] for r in log if r["type"] == "START"}
    assert all(starts[k] >= analysis.est[k] for k in starts)  # раньше самого раннего старта нельзя
    assert max(r["t"] for r in log if r["type"] == "FINISH") >= analysis.lb


def test_online_demo_with_a_submitted_urgent_task(demo_problem, greedy, check_capacity):
    problem = online(demo_problem)
    sim = Simulator(problem, greedy)
    log = sim.run_all()
    assert set(sim.state.status.values()) == {TaskStatus.SUCCEEDED}
    assert [r["t"] for r in log if r["type"] == "SUBMIT"] == [9]
    check_capacity(problem, log)


def test_two_runs_give_the_same_journal_hash(demo_problem, greedy):
    first, second = Simulator(online(demo_problem), greedy), Simulator(online(demo_problem), greedy)
    first.run_all()
    second.run_all()
    assert first.journal.result_hash() == second.journal.result_hash()


def test_order_of_tasks_and_workers_in_the_input_does_not_matter(demo_problem):
    original, permuted = (
        Simulator(demo_problem, FirstFreeWorker()),
        Simulator(reordered(demo_problem), FirstFreeWorker()),
    )
    original.run_all()
    permuted.run_all()
    assert original.journal.result_hash() == permuted.journal.result_hash()
