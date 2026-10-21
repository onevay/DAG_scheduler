import pytest

from expsched.domain.models import Event, EvType, Task
from expsched.domain.states import EvClass
from expsched.engine.events import EventQueue, Finish, Release, event_class

DOWN = Event(5, EvType.WORKER_DOWN, worker="a")
UP = Event(5, EvType.WORKER_UP, worker="a")
SUBMIT = Event(5, EvType.SUBMIT, task=Task("urgent", 1))


def test_classes_of_payloads():
    assert event_class(Finish("t", 1)) is EvClass.FINISH
    assert event_class(DOWN) is EvClass.WORKER_DOWN
    assert event_class(UP) is EvClass.WORKER_UP
    assert event_class(SUBMIT) is EvClass.QUEUE
    assert event_class(Release("t")) is EvClass.QUEUE


def test_same_moment_is_ordered_by_class_not_by_insertion():
    q = EventQueue()
    release = Release("r")
    for payload in (SUBMIT, release, UP, DOWN, Finish("t", 1)):  # намеренно в обратном порядке
        q.push(5, payload)
    _, batch = q.pop_batch()
    assert batch == [Finish("t", 1), DOWN, UP, SUBMIT, release]


def test_finish_is_handled_before_failure_in_the_same_moment():
    q = EventQueue()
    q.push(5, DOWN)
    q.push(5, Finish("t", 1))
    _, batch = q.pop_batch()
    assert batch[0] == Finish("t", 1)  # задача, закончившаяся в момент отказа, успешна


def test_same_class_keeps_insertion_order():
    q = EventQueue()
    first, second, third = Finish("b", 1), Finish("a", 1), Finish("c", 1)
    for payload in (first, second, third):
        q.push(3, payload)
    assert q.pop_batch() == (3, [first, second, third])


def test_pop_batch_takes_only_the_earliest_moment():
    q = EventQueue()
    q.push(7, Finish("late", 1))
    q.push(2, Finish("early1", 1))
    q.push(2, Release("early2"))
    assert len(q) == 3
    assert q.pop_batch() == (2, [Finish("early1", 1), Release("early2")])
    assert len(q) == 1
    assert q.pop_batch() == (7, [Finish("late", 1)])
    assert len(q) == 0


def test_events_pushed_while_draining_join_their_own_moment():
    q = EventQueue()
    q.push(1, Finish("a", 1))
    q.pop_batch()
    q.push(4, Finish("b", 1))
    q.push(2, Finish("c", 1))
    assert [q.pop_batch()[0], q.pop_batch()[0]] == [2, 4]


def test_empty_queue_raises():
    with pytest.raises(IndexError):
        EventQueue().pop_batch()
