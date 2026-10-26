"""Shadow: план рядом с фактом прогона на агентах и причины расхождений."""

from test_emulation import Loop, laptop

from expsched.contract.output_models import ResultOut


def shadow_of(emulation):
    assert emulation.result is not None
    result = ResultOut.model_validate(emulation.result.result)  # shadow — часть результата по схеме
    assert result.shadow is not None
    return result.shadow


def causes(shadow):
    return {key: task.cause for key, task in shadow.tasks.items()}


def test_undisturbed_run_matches_its_plan(tmp_path):
    shadow = shadow_of(Loop(tmp_path, laptop(mode="shadow")).run())
    assert (shadow.plan_makespan, shadow.fact_makespan) == (90, 90)
    assert set(causes(shadow).values()) == {"same"}


def test_failed_container_and_everything_after_it(tmp_path):
    loop = Loop(tmp_path, laptop(mode="shadow"))
    while not loop.runtime.containers:
        loop.step()
    loop.runtime.oom(sorted(loop.runtime.containers)[0])  # load_data — первая задача
    shadow = shadow_of(loop.run())
    assert causes(shadow) == {
        "load_data": "failure",
        "tokenize": "upstream",
        "embed_index": "upstream",
        "train_small": "upstream",
        "eval_small": "upstream",
        "report": "upstream",
    }
    assert shadow.fact_makespan > shadow.plan_makespan and shadow.tasks["report"].finish_delta == 1


def test_manual_node_failure_is_told_apart_and_live_view_waits(tmp_path):
    loop = Loop(tmp_path, laptop(mode="shadow"))
    for _ in range(40):  # 20 единиц модели: идут tokenize и embed_index
        loop.step()
    live = loop.emulation.shadow()
    assert (
        live is not None and live["causes"]["waiting"] == 5 and live["tasks"]["load_data"]["cause"] == "same"
    )
    loop.emulation.drop("laptop")
    for _ in range(10):
        loop.step()
    loop.emulation.restore("laptop")
    shadow = shadow_of(loop.run())
    assert causes(shadow)["tokenize"] == causes(shadow)["embed_index"] == "manual"
    assert causes(shadow)["report"] in ("upstream", "decision")


def test_emulate_without_shadow_has_no_plan(tmp_path):
    emulation = Loop(tmp_path, laptop()).run()
    assert emulation.shadow() is None and emulation.result is not None
    assert "shadow" not in emulation.result.result
