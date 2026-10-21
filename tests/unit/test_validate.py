import dataclasses
import json

import pytest

from expsched.analysis import analyze
from expsched.contract.limits import MAX_BODY_BYTES, InputTooLarge
from expsched.domain.models import Task
from expsched.domain.resources import Resources
from expsched.domain.states import TaskStatus
from expsched.validation import Code, has_errors, quarantine, validate, validate_problem
from expsched.validation.deadlines import deadline_certificates
from expsched.validation.schema import MAX_SCHEMA_DIAGNOSTICS


def codes(diagnostics):
    return [d.code for d in diagnostics]


def raw(**over):
    base = {
        "cluster": {"workers": [{"id": "n", "resources": {"gpu": 4}}]},
        "tasks": [{"id": "a", "duration": 2, "resources": {"gpu": 1}}],
    }
    return json.dumps({**base, **over}).encode()


# ---- E030 ----


def test_e030_only_for_the_task_with_its_own_deadline(make_task, make_problem):
    # цепочка a -> b -> c по 4 часа, дедлайн у c: EFT = 12 > 9. Предки не получают своих E030.
    problem = make_problem([make_task("a", 4), make_task("b", 4, "a"), make_task("c", 4, "b", deadline=9)])
    (d,) = deadline_certificates(problem, analyze(problem))
    assert d.code is Code.E030
    assert d.detail == {"task": "c", "earliest_finish": 12, "deadline": 9}


def test_e030_silent_when_deadlines_are_reachable(demo_problem):
    assert deadline_certificates(demo_problem, analyze(demo_problem)) == []


# ---- quarantine ----


def test_quarantine_marks_cycle_members_infeasible_and_descendants_blocked(make_task, make_problem):
    problem = make_problem(
        [
            make_task("a", 1, "c"),
            make_task("b", 1, "a"),
            make_task("c", 1, "b"),
            make_task("after", 1, "c"),
            make_task("ghost_user", 1, "ghost"),
            make_task("after_ghost", 1, "ghost_user"),
            make_task("fine"),
        ]
    )
    _, diagnostics = validate_problem(problem, "lenient")
    q = quarantine(problem, diagnostics)
    assert q.excluded == {
        "a": TaskStatus.INFEASIBLE,
        "b": TaskStatus.INFEASIBLE,
        "c": TaskStatus.INFEASIBLE,
        "ghost_user": TaskStatus.INFEASIBLE,
        "after": TaskStatus.BLOCKED,
        "after_ghost": TaskStatus.BLOCKED,
    }
    assert list(q.problem.tasks) == ["fine"]


def test_quarantine_covers_the_whole_component_not_just_the_reported_cycle(make_task, make_problem):
    # две петли a<->b и b<->c образуют одну компоненту {a, b, c}; E012 называет лишь один цикл
    problem = make_problem([make_task("a", 1, "b"), make_task("b", 1, "a", "c"), make_task("c", 1, "b")])
    _, diagnostics = validate_problem(problem, "lenient")
    assert codes(diagnostics) == [Code.E012]
    assert set(quarantine(problem, diagnostics).excluded) == {"a", "b", "c"}


def test_quarantined_problem_is_acyclic_and_closed_under_predecessors(make_task, make_problem):
    problem = make_problem(
        [
            make_task("a", 1, "b"),
            make_task("b", 1, "a"),
            make_task("c", 1, "b"),
            make_task("d"),
            make_task("e", 1, "d"),
        ]
    )
    _, diagnostics = validate_problem(problem, "lenient")
    runnable = quarantine(problem, diagnostics).problem
    analyze(runnable)  # не бросает: циклов нет
    assert all(dep in runnable.tasks for t in runnable.tasks.values() for dep in t.deps)


def test_ineligible_task_blocks_its_descendants(make_task, make_problem):
    problem = make_problem([make_task("huge", 1, gpu=8), make_task("next", 1, "huge"), make_task("ok")])
    _, diagnostics = validate_problem(problem, "lenient")
    q = quarantine(problem, diagnostics)
    assert q.excluded == {"huge": TaskStatus.INFEASIBLE, "next": TaskStatus.BLOCKED}


def test_nothing_is_quarantined_for_a_clean_problem(demo_problem):
    result, diagnostics = validate_problem(demo_problem, "lenient")
    q = quarantine(demo_problem, diagnostics)
    assert result == demo_problem and diagnostics == []
    assert q.excluded == {} and q.problem == demo_problem


# ---- validate_problem: режимы ----


def test_strict_mode_returns_none_on_any_error(make_task, make_problem):
    problem = make_problem([make_task("a", 1, "ghost")])
    result, diagnostics = validate_problem(problem, "strict")
    assert result is None and codes(diagnostics) == [Code.E010]


def test_lenient_mode_keeps_the_problem_and_reports(make_task, make_problem):
    problem = make_problem([make_task("a", 1, "ghost")])
    result, diagnostics = validate_problem(problem, "lenient")
    assert result is problem and has_errors(diagnostics)


def test_warnings_do_not_fail_strict_mode_and_duplicates_are_removed(make_task, make_problem):
    problem = make_problem([make_task("a"), make_task("b", 1, "a", "a")])
    result, diagnostics = validate_problem(problem, "strict")
    assert codes(diagnostics) == [Code.W060]
    assert result is not None and result.tasks["b"].deps == ("a",)


def test_resource_error_and_unreachable_deadline_together(demo_problem):
    """Задача на 8 GPU даёт E020, недостижимый дедлайн после distill — ровно одну E030."""
    tasks = dict(demo_problem.tasks)
    tasks["huge"] = Task("huge", 2, Resources({"gpu": 8}))
    tasks["late_report"] = Task("late_report", 2, Resources({"gpu": 1}), deps=("distill",), deadline=19)
    problem = dataclasses.replace(demo_problem, tasks=tasks)
    result, diagnostics = validate_problem(problem, "lenient")
    assert result is problem
    e020, e030 = diagnostics
    assert (e020.code, e020.detail) == (Code.E020, {"task": "huge", "resource": "gpu", "need": 8, "max": 4})
    assert (e030.code, e030.detail) == (
        Code.E030,
        {"task": "late_report", "earliest_finish": 20, "deadline": 19},
    )


# ---- validate: сырой вход ----


def test_valid_raw_input_passes():
    problem, diagnostics = validate(raw(), "strict")
    assert diagnostics == [] and problem is not None and set(problem.tasks) == {"a"}


def test_unknown_field_is_e001_with_location():
    problem, diagnostics = validate(raw(typo=1), "lenient")
    assert problem is None
    assert [(d.code, d.detail["loc"]) for d in diagnostics] == [(Code.E001, "typo")]


def test_non_positive_duration_is_e070():
    problem, diagnostics = validate(raw(tasks=[{"id": "a", "duration": 0}]), "lenient")
    assert problem is None and diagnostics[0].code is Code.E070
    assert diagnostics[0].detail["loc"] == "tasks.0.duration"


def test_broken_json_is_e001():
    _, diagnostics = validate(b"{oops", "lenient")
    assert codes(diagnostics) == [Code.E001]


def test_schema_diagnostics_are_capped():
    tasks = [{"id": f"t{i}", "duration": 1, "bogus": 1} for i in range(MAX_SCHEMA_DIAGNOSTICS + 20)]
    _, diagnostics = validate(raw(tasks=tasks), "lenient")
    assert len(diagnostics) == MAX_SCHEMA_DIAGNOSTICS + 1
    assert diagnostics[-1].detail == {"omitted": 20}


def test_oversized_input_is_not_a_diagnostic():
    with pytest.raises(InputTooLarge):
        validate(b" " * (MAX_BODY_BYTES + 1), "lenient")


def test_diagnostic_serialises_to_result_shape():
    _, diagnostics = validate(raw(tasks=[{"id": "a", "duration": 1, "dependencies": ["zz"]}]), "lenient")
    assert diagnostics[0].to_dict() == {
        "code": "E010",
        "level": "error",
        "detail": {"task": "a", "dep": "zz"},
        "hint": diagnostics[0].hint,
    }
    json.dumps(diagnostics[0].to_dict())
