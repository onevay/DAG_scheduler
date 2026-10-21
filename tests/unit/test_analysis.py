import dataclasses

import pytest

from expsched.analysis import analyze
from expsched.analysis.bounds import energy_bounds, path_bound, threshold_bound


def test_demo_analysis_offline(demo_problem):
    """Сроки и ранги демо-DAG, посчитанные по длительностям и зависимостям."""
    a = analyze(demo_problem)
    assert a.est == {
        "prep_data": 0,
        "pretrain_vit": 2,
        "train_asr": 2,
        "eval_asr": 8,
        "ft_det": 10,
        "ft_seg": 10,
        "distill": 14,
        "eval_det": 14,
        "eval_seg": 15,
        "infer_bench": 18,
        "urgent_eval": 10,
    }
    assert a.eft["infer_bench"] == 21 and a.eft["urgent_eval"] == 13
    assert a.rank == {
        "prep_data": 21,
        "pretrain_vit": 19,
        "train_asr": 11,
        "eval_asr": 5,
        "ft_det": 11,
        "ft_seg": 7,
        "distill": 7,
        "eval_det": 5,
        "eval_seg": 2,
        "infer_bench": 3,
        "urgent_eval": 3,
    }
    # дедлайн срочной задачи распространяется на предков, запас у всей цепочки ровно 1
    slack = {k: v - a.est[k] for k, v in a.lst.items() if v is not None}
    assert a.lst["urgent_eval"] == 11 and a.lst["eval_asr"] == 9
    assert a.lst["train_asr"] == 3 and a.lst["prep_data"] == 1
    assert slack == {"prep_data": 1, "train_asr": 1, "eval_asr": 1, "urgent_eval": 1}
    assert a.lft["pretrain_vit"] is None and a.lst["infer_bench"] is None


def test_demo_lower_bounds(demo_problem):
    a = analyze(demo_problem)
    assert a.lb_path == 21
    assert a.lb_energy == {"gpu": 9}  # ⌈87 / 10⌉
    assert a.lb == 21


def test_online_ranks_without_the_urgent_task(demo_problem):
    online = dataclasses.replace(
        demo_problem, tasks={k: t for k, t in demo_problem.tasks.items() if k != "urgent_eval"}
    )
    a = analyze(online)
    assert a.rank["train_asr"] == 8 and a.rank["eval_asr"] == 2
    assert all(v is None for v in a.lst.values())  # дедлайнов нет


def test_order_is_topological(demo_problem):
    a = analyze(demo_problem)
    position = {k: i for i, k in enumerate(a.order)}
    for key, task in demo_problem.tasks.items():
        assert all(position[d] < position[key] for d in task.deps)


def test_done_tasks_are_excluded_and_remaining_work_is_used(demo_problem):
    a = analyze(demo_problem, remaining={"pretrain_vit": 6}, now=5, done={"prep_data"})
    assert "prep_data" not in a.order
    assert a.est["pretrain_vit"] == 5 and a.eft["pretrain_vit"] == 11
    assert a.est["train_asr"] == 5  # ссылка на завершённую prep_data игнорируется
    assert a.est["eval_asr"] == 11 and a.est["urgent_eval"] == 13  # поздний release не мешает цепочке


def test_release_time_delays_earliest_start(make_task, make_problem):
    a = analyze(make_problem([make_task("a", 2, release=7), make_task("b", 1, "a")]))
    assert (a.est["a"], a.eft["a"], a.est["b"], a.lb) == (7, 9, 9, 10)


def test_cyclic_graph_is_rejected(make_task, make_problem):
    problem = make_problem([make_task("a", 1, "b"), make_task("b", 1, "a")])
    with pytest.raises(ValueError, match="cycle"):
        analyze(problem)


def test_empty_problem(make_problem):
    a = analyze(make_problem([]), now=3)
    assert a.order == () and a.lb == 3


def test_threshold_bound_counts_only_nodes_big_enough(make_task, make_problem):
    # две задачи по 4 GPU и 4 часа; 4-GPU узел один (второй на 2 GPU): вместе 8 часов, а не 4
    problem = make_problem([make_task("x", 4, gpu=4), make_task("y", 4, gpu=4)], gpus=(4, 2))
    a = analyze(problem)
    assert a.lb_path == 4 and a.lb_energy == {"gpu": 6} and a.lb_thr == 8 and a.lb == 8


def test_bound_helpers_on_empty_input():
    assert path_bound({}, 5) == 5
    assert energy_bounds({}, {}, [], 5) == {}
    assert threshold_bound({}, {}, [], 5) == 5
