import pytest

from expsched.domain.models import Problem, Task, Worker
from expsched.domain.resources import Resources
from expsched.domain.states import FINAL_STATUSES, EvClass, PriorityClass, TaskStatus, priority_class


@pytest.mark.parametrize(
    ("priority", "cls"),
    [
        (0, PriorityClass.LOW),
        (1, PriorityClass.LOW),
        (2, PriorityClass.NORMAL),
        (4, PriorityClass.NORMAL),
        (5, PriorityClass.HIGH),
        (7, PriorityClass.HIGH),
        (8, PriorityClass.CRITICAL),
        (10, PriorityClass.CRITICAL),
    ],
)
def test_priority_class_boundaries(priority, cls):
    assert priority_class(priority) == cls
    assert Task("t", 1, priority=priority).cls == cls


@pytest.mark.parametrize(
    "kwargs", [{"duration": 0}, {"priority": 11}, {"priority": -1}, {"ckpt": 0}, {"release": -1}]
)
def test_task_rejects_invalid_values(kwargs):
    with pytest.raises(ValueError):
        Task("t", **{"duration": 1, **kwargs})


def test_event_classes_order_finish_before_failure():
    assert EvClass.FINISH < EvClass.WORKER_DOWN < EvClass.WORKER_UP < EvClass.QUEUE < EvClass.DECIDE


def test_final_statuses_are_absorbing_set():
    assert TaskStatus.SUCCEEDED in FINAL_STATUSES
    assert not {TaskStatus.PENDING, TaskStatus.READY, TaskStatus.RUNNING} & FINAL_STATUSES


def test_eligible_filters_by_capacity_and_labels():
    workers = {
        "a": Worker("a", Resources({"gpu": 4}), (("gpu_type", "a100"),)),
        "b": Worker("b", Resources({"gpu": 4}), (("gpu_type", "t4"),)),
        "c": Worker("c", Resources({"gpu": 2}), (("gpu_type", "a100"),)),
    }
    p = Problem({}, workers)
    assert p.eligible(Task("big", 1, Resources({"gpu": 4}))) == ("a", "b")
    pinned = Task("pin", 1, Resources({"gpu": 2}), constraints=(("gpu_type", "a100"),))
    assert p.eligible(pinned) == ("a", "c")
    assert p.eligible(Task("huge", 1, Resources({"gpu": 8}))) == ()
    assert p.eligible(Task("free", 1)) == ("a", "b", "c")
