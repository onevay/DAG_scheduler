from expsched_ui import dag

PROBLEM = {
    "tasks": [
        {"id": "a", "duration": 2, "resources": {"gpu": 1}},
        {"id": "b", "duration": 3, "dependencies": ["a", "ghost"]},
    ],
    "events": [{"time": 4, "type": "submit_task", "task": {"id": "u", "duration": 1, "dependencies": ["b"]}}],
}


def test_tasks_edges_and_submitted_tasks_are_drawn():
    dot = dag.dependency_dot(PROBLEM)
    assert '"a" -> "b";' in dot and '"b" -> "u";' in dot
    assert '"a" [label="a\\n2 · 1 GPU"' in dot
    assert f'"u" [label="u\\n1 · 0 GPU", fillcolor="{dag.SUBMITTED}"]' in dot


def test_unknown_dependency_is_drawn_dashed():
    assert f'"ghost" [style="dashed", fillcolor="{dag.PROBLEM}"];' in dag.dependency_dot(PROBLEM)


def test_tasks_with_errors_are_highlighted_but_warnings_are_not():
    diagnostics = [
        {"code": "E010", "level": "error", "detail": {"task": "b", "dep": "ghost"}},
        {"code": "E012", "level": "error", "detail": {"cycle": ["x", "y", "x"]}},
        {"code": "W060", "level": "warning", "detail": {"task": "a", "dep": "z", "count": 2}},
    ]
    marked = dag.problem_tasks(diagnostics)
    assert marked == {"b", "x", "y"}
    assert f'"b" [label="b\\n3 · 0 GPU", fillcolor="{dag.PROBLEM}"]' in dag.dependency_dot(PROBLEM, marked)


def test_repeated_dependency_is_one_edge():
    problem = {"tasks": [{"id": "a", "duration": 1}, {"id": "b", "duration": 1, "dependencies": ["a", "a"]}]}
    assert dag.dependency_dot(problem).count('"a" -> "b";') == 1
