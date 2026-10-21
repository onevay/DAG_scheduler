from expsched.domain.models import Worker
from expsched.domain.resources import Resources
from expsched.validation.diagnostics import Code, has_errors
from expsched.validation.graph import dedupe_dependencies, validate_graph
from expsched.validation.resources import validate_resources


def codes(diagnostics):
    return [d.code for d in diagnostics]


def tasks_of(*tasks):
    return {t.id: t for t in tasks}


# ---- граф: E010, E011, E012, W060 ----


def test_cycle_is_reported_with_path_and_unknown_dependency(make_task):
    tasks = tasks_of(make_task("a", 1, "c"), make_task("b", 1, "a"), make_task("c", 1, "b", "ghost"))
    unknown, cycle = validate_graph(tasks)
    assert (unknown.code, unknown.detail) == (Code.E010, {"task": "c", "dep": "ghost"})
    assert cycle.code is Code.E012
    assert cycle.detail["cycle"] == ["a", "b", "c", "a"]
    assert "a → b → c → a" in cycle.hint and "эвристика" in cycle.hint


def test_cycle_hint_recommends_edge_into_the_task_with_most_other_dependencies(make_task):
    tasks = tasks_of(
        make_task("p"),
        make_task("a", 1, "c"),
        make_task("b", 1, "a"),
        make_task("c", 1, "b", "p"),  # c зависит ещё и от p: разрыв b -> c оставит её упорядоченной
    )
    (cycle,) = validate_graph(tasks)
    assert "уберите b из зависимостей c" in cycle.hint


def test_self_dependency_is_e011_and_not_a_cycle(make_task):
    diagnostics = validate_graph(tasks_of(make_task("a", 1, "a"), make_task("b", 1, "a")))
    assert codes(diagnostics) == [Code.E011]


def test_two_node_cycle_and_independent_cycles_each_get_one_e012(make_task):
    tasks = tasks_of(
        make_task("a", 1, "b"),
        make_task("b", 1, "a"),
        make_task("x", 1, "z"),
        make_task("y", 1, "x"),
        make_task("z", 1, "y"),
        make_task("ok"),
    )
    diagnostics = validate_graph(tasks)
    assert codes(diagnostics) == [Code.E012, Code.E012]
    assert [d.detail["cycle"] for d in diagnostics] == [["a", "b", "a"], ["x", "y", "z", "x"]]


def test_duplicate_edge_is_a_warning_and_is_removed(make_task, make_problem):
    problem = make_problem([make_task("a"), make_task("b", 1, "a", "a")])
    (warning,) = validate_graph(problem.tasks)
    assert warning.code is Code.W060 and warning.level == "warning"
    assert warning.detail == {"task": "b", "dep": "a", "count": 2}
    assert not has_errors([warning])
    assert dedupe_dependencies(problem).tasks["b"].deps == ("a",)


def test_dedupe_returns_same_object_when_nothing_to_fix(make_task, make_problem):
    problem = make_problem([make_task("a"), make_task("b", 1, "a")])
    assert dedupe_dependencies(problem) is problem


def test_clean_graph_has_no_diagnostics(demo_problem):
    assert validate_graph(demo_problem.tasks) == []


# ---- ресурсы: E020, E021 ----


def test_e020_reports_need_and_best_available(make_task, make_problem):
    problem = make_problem([make_task("huge", 1, gpu=8)], gpus=(4, 2))
    (d,) = validate_resources(problem)
    assert d.code is Code.E020
    assert d.detail == {"task": "huge", "resource": "gpu", "need": 8, "max": 4}


def test_e021_when_no_worker_has_the_labels(make_task, make_problem):
    problem = make_problem([make_task("t", 1, constraints=(("gpu_type", "h100"),))])
    (d,) = validate_resources(problem)
    assert d.code is Code.E021 and d.detail["constraints"] == {"gpu_type": "h100"}


def test_e020_is_measured_against_workers_matching_the_labels(make_task):
    from expsched.domain.models import Problem

    workers = {
        "small": Worker("small", Resources({"gpu": 2}), (("gpu_type", "a100"),)),
        "big": Worker("big", Resources({"gpu": 8}), (("gpu_type", "t4"),)),
    }
    task = make_task("t", 1, gpu=4, constraints=(("gpu_type", "a100"),))
    (d,) = validate_resources(Problem({"t": task}, workers))
    assert d.code is Code.E020 and d.detail["max"] == 2


def test_e021_when_resources_fit_separately_but_not_together(make_task):
    from expsched.domain.models import Problem, Task

    workers = {
        "gpu-node": Worker("gpu-node", Resources({"gpu": 4, "ram_gb": 8})),
        "ram-node": Worker("ram-node", Resources({"gpu": 1, "ram_gb": 64})),
    }
    task = Task("t", 1, Resources({"gpu": 4, "ram_gb": 32}))
    (d,) = validate_resources(Problem({"t": task}, workers))
    assert d.code is Code.E021 and "сразу" in d.hint


def test_task_without_resources_fits_anywhere(make_task, make_problem):
    assert validate_resources(make_problem([make_task("t", 1, gpu=0)])) == []
