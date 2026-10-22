import pytest

from expsched.domain.snapshot import Decision, Snapshot
from expsched.engine.errors import InvalidDecision
from expsched.engine.simulator import Simulator
from expsched.verify.invariants import verify


class HeadReservation:
    """Узел n0. Задачи по id, пока помещаются; первой непомещающейся — резерв к окончанию всего, что на узле
    идёт или стартовало в эту эпоху. После резерва ничего не стартует: для проверки движка этого достаточно.
    """

    name = "head-reservation-test"

    def decide(self, snapshot: Snapshot) -> list[Decision]:
        free = snapshot.free["n0"]
        ends = [run.end_pred for run in snapshot.running.values()]
        out = []
        for key in snapshot.ready:
            need = snapshot.tasks[key].resources
            if need.fits(free):
                free = free - need
                ends.append(snapshot.t + snapshot.rem[key])
                out.append(Decision("START", key, "n0"))
                continue
            out.append(Decision("RESERVE", key, "n0", {"t_res": max(ends, default=snapshot.t)}))
            break
        return out


class Fixed:
    """Возвращает одни и те же решения."""

    name = "fixed-test"

    def __init__(self, *decisions: Decision) -> None:
        self.decisions = list(decisions)

    def decide(self, snapshot: Snapshot) -> list[Decision]:
        return self.decisions


def records(log, kind):
    return [r for r in log if r["type"] == kind]


def wait_codes(log, task):
    return [(r["t"], r["code"]) for r in records(log, "WAIT") if r["task"] == task]


@pytest.fixture
def held(make_task, make_problem):
    """a занимает половину узла до 4; big ждёт весь узел; small и late поместились бы, но их держит резерв."""
    tasks = [
        make_task("a", 4, gpu=2),
        make_task("big", 2, gpu=4, release=1),
        make_task("small", 5, gpu=1, release=1),
        make_task("late", 1, gpu=1, release=2),
    ]
    problem = make_problem(tasks, gpus=(4,))
    return problem, Simulator(problem, HeadReservation()).run_all()


def test_reservation_is_written_once_while_it_does_not_change(held):
    _, log = held
    # в t = 2 (release задачи late) резерв тот же, повторной записи нет; в t = 4 стартует big, головной — late
    assert [(r["t"], r["task"], r["worker"], r["t_res"]) for r in records(log, "RESERVE")] == [
        (1, "big", "n0", 4),
        (4, "late", "n0", 6),
    ]
    assert [(r["t"], r["task"]) for r in records(log, "START")] == [
        (0, "a"),
        (4, "big"),
        (6, "late"),
        (6, "small"),
    ]


def test_tasks_held_for_the_reservation_say_so(held):
    _, log = held
    detail = {"head": "big", "worker": "n0", "t_res": 4}
    small = [(r["t"], r["code"], r["detail"]) for r in records(log, "WAIT") if r["task"] == "small"]
    assert small[1] == (1, "WAIT_RESERVATION", detail)
    assert wait_codes(log, "late")[1] == (2, "WAIT_RESERVATION")
    # сама головная задача ждёт ресурсы, а после старта big место у остальных отняла она
    assert wait_codes(log, "big") == [(0, "WAIT_RELEASE"), (1, "WAIT_RESOURCES")]
    assert wait_codes(log, "small")[2] == (4, "WAIT_PRIORITY")


def test_reservations_do_not_affect_the_schedule_check(held):
    problem, log = held
    assert verify(problem, log) == []


def test_new_reservation_is_written(make_task, make_problem):
    tasks = [make_task("a", 2, gpu=4), make_task("big", 1, gpu=4), make_task("c", 3, gpu=4, release=1)]
    log = Simulator(make_problem(tasks, gpus=(4,)), HeadReservation()).run_all()
    # в t = 0 big ждёт a (t_res = 2), в t = 1 резерв тот же; в t = 2 big стартует, и головной становится c
    assert [(r["t"], r["task"], r["t_res"]) for r in records(log, "RESERVE")] == [(0, "big", 2), (2, "c", 3)]


@pytest.mark.parametrize(
    ("decision", "message"),
    [
        (Decision("RESERVE", "b", "n0", {"t_res": 5}), "not READY"),
        (Decision("RESERVE", "a", "n1", {"t_res": 5}), "not eligible"),
        (Decision("RESERVE", "a", "n9", {"t_res": 5}), "unknown or down"),
        (Decision("RESERVE", "a", "n0", {}), "t_res"),
        (Decision("RESERVE", "a", "n0", {"t_res": -1}), "t_res"),
    ],
)
def test_invalid_reservations_are_rejected(make_task, make_problem, decision, message):
    problem = make_problem([make_task("a", 1, gpu=4), make_task("b", 1, "a")], gpus=(4, 2))
    with pytest.raises(InvalidDecision, match=message):
        Simulator(problem, Fixed(decision)).run_all()
