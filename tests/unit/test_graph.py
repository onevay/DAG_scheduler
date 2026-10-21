import time

import pytest

from expsched.domain.graph import TaskGraph
from expsched.domain.models import Task


def graph_of(make_task, edges: dict[str, list[str]]) -> TaskGraph:
    """edges: задача -> её зависимости."""
    return TaskGraph({k: make_task(k, 1, *deps) for k, deps in edges.items()})


def test_edges_in_both_directions_sorted(make_task):
    g = graph_of(make_task, {"a": [], "b": ["a"], "c": ["a", "b"]})
    assert g.nodes == ("a", "b", "c")
    assert g.succs("a") == ("b", "c") and g.preds("c") == ("a", "b")


def test_loops_unknown_refs_and_duplicates_are_not_edges(make_task):
    g = graph_of(make_task, {"a": ["a", "ghost"], "b": ["a", "a"]})
    assert g.preds("a") == () and g.preds("b") == ("a",)
    assert g.topological_order() == ["a", "b"]


def test_topological_order_respects_dependencies(make_task):
    g = graph_of(make_task, {"d": ["b", "c"], "b": ["a"], "c": ["a"], "a": []})
    order = g.topological_order()
    assert order == ["a", "b", "c", "d"]


def test_topological_order_is_partial_on_cycle(make_task):
    g = graph_of(make_task, {"a": [], "b": ["a", "c"], "c": ["b"], "d": ["c"]})
    assert g.topological_order() == ["a"]  # b, c в цикле, d за ним


def test_components_find_every_independent_cycle(make_task):
    g = graph_of(
        make_task,
        {"a": ["c"], "b": ["a"], "c": ["b"], "x": ["y"], "y": ["x"], "ok": [], "tail": ["c"]},
    )
    assert g.cyclic_components() == [["a", "b", "c"], ["x", "y"]]


def test_find_cycle_returns_closed_simple_path(make_task):
    g = graph_of(make_task, {"a": ["c"], "b": ["a"], "c": ["b"]})
    assert g.find_cycle(["a", "b", "c"]) == ["a", "b", "c", "a"]


def test_find_cycle_drops_the_tail_before_the_cycle(make_task):
    # a -> b -> c -> d -> b: из компоненты {b, c, d} цикл не должен включать a
    g = graph_of(make_task, {"a": [], "b": ["a", "d"], "c": ["b"], "d": ["c"]})
    (component,) = g.cyclic_components()
    cycle = g.find_cycle(component)
    assert cycle[0] == cycle[-1] and set(cycle) == {"b", "c", "d"} and len(cycle) == 4


def test_descendants_exclude_the_root_unless_it_is_on_a_cycle(make_task):
    g = graph_of(make_task, {"a": [], "b": ["a"], "c": ["b"], "x": ["y"], "y": ["x"]})
    assert g.descendants(["a"]) == {"b", "c"}
    assert g.descendants(["x"]) == {"x", "y"}
    assert g.descendants([]) == set()


def test_empty_graph(make_task):
    g = TaskGraph({})
    assert len(g) == 0 and g.topological_order() == [] and g.cyclic_components() == []


@pytest.mark.parametrize("shape", ["chain", "ring"])
def test_large_graphs_are_handled_without_recursion(shape):
    n = 100_000
    deps = (lambda i: (f"t{i - 1}",) if i else ()) if shape == "chain" else (lambda i: (f"t{(i - 1) % n}",))
    tasks = {f"t{i}": Task(f"t{i}", 1, deps=deps(i)) for i in range(n)}
    start = time.perf_counter()
    g = TaskGraph(tasks)
    components = g.cyclic_components()
    order = g.topological_order()
    assert time.perf_counter() - start < 10  # запас на медленный CI: ловим квадратичную асимптотику
    if shape == "chain":
        assert components == [] and len(order) == n
    else:
        assert len(components) == 1 and len(components[0]) == n and order == []
