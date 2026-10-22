"""Интерфейс планировщика: вход, проверка, прогон и его результат. Запуск: `make api`, затем `make ui`."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from expsched_ui import dag, results, tables
from expsched_ui.client import ApiClient, ApiUnavailable

SCENARIOS = Path(__file__).resolve().parents[2] / "scenarios"
OFFLINE_POLICIES = {"policies": ["fifo", "fifo_skip", "priority"], "defaults": {"aging": None}}

st.set_page_config(page_title="Планировщик экспериментов", layout="wide")
client = ApiClient(os.environ.get("EXPSCHED_API", "http://127.0.0.1:8000"))


@st.cache_data(ttl=60)
def policy_options() -> dict[str, Any]:
    """Политики и параметры по умолчанию из API; без API — минимальный список, чтобы форма работала."""
    try:
        return client.policies()
    except ApiUnavailable:
        return OFFLINE_POLICIES


def load(problem: dict[str, Any]) -> None:
    """Новый вход: таблицы-редакторы пересоздаются (новый ключ), прошлые результаты сбрасываются."""
    st.session_state.problem = problem
    st.session_state.revision = st.session_state.get("revision", 0) + 1
    for stale in ("diagnostics", "run", "comparison"):
        st.session_state.pop(stale, None)


def editor(
    rows: list[dict[str, Any]], columns: tuple[str, ...], name: str, config: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    frame = pd.DataFrame(rows, columns=list(columns))
    edited = st.data_editor(
        frame,
        num_rows="dynamic",
        width="stretch",
        column_config=config,
        key=f"{name}-{st.session_state.revision}",
    )
    return list(edited.to_dict("records"))


DEFAULT_SAMPLE = "demo"
if "problem" not in st.session_state:
    load(json.loads((SCENARIOS / f"{DEFAULT_SAMPLE}.json").read_text(encoding="utf-8")))

with st.sidebar:
    st.header("Вход")
    samples = sorted(p.stem for p in SCENARIOS.glob("*.json"))
    sample = st.selectbox("Пример", samples, index=samples.index(DEFAULT_SAMPLE))
    if st.button("Загрузить пример", width="stretch"):
        load(json.loads((SCENARIOS / f"{sample}.json").read_text(encoding="utf-8")))
    uploaded = st.file_uploader("Свой problem.json", type="json")
    if uploaded is not None and st.button("Загрузить файл", width="stretch"):
        load(json.loads(uploaded.getvalue()))
    if st.button("Начать с нуля", width="stretch"):
        load(tables.EMPTY_PROBLEM)

problem = st.session_state.problem
policy_in = problem.get("policy", {})
st.title("Планировщик вычислительных экспериментов")
tab_workers, tab_tasks, tab_submits, tab_node_events, tab_settings, tab_graph = st.tabs(
    ["Узлы", "Задачи", "Срочные задачи", "События узлов", "Параметры", "Граф зависимостей"]
)

with tab_workers:
    st.caption("Ресурсы — целые числа. Метки — пары «ключ=значение» через запятую, например gpu_type=a100.")
    workers = editor(tables.workers_rows(problem), tables.WORKER_COLUMNS, "workers")
with tab_tasks:
    st.caption(
        "Зависимости — id задач через запятую. Ограничения — метки, которые обязаны быть у узла. "
        "Приоритет 0–10: 8–10 critical, 5–7 high, 2–4 normal, 0–1 low."
    )
    tasks = editor(tables.tasks_rows(problem), tables.TASK_COLUMNS, "tasks")
with tab_submits:
    st.caption("Задачи, которые появляются во время прогона: time — момент подачи.")
    submits = editor(tables.submits_rows(problem), tables.SUBMIT_COLUMNS, "submits")
with tab_node_events:
    st.caption(
        "down — узел отказал: его задачи прерываются и продолжают с последнего чекпоинта на другом узле; "
        "up — узел вернулся пустым. max_retries у задачи — сколько отказов она переживёт."
    )
    node_events = editor(
        tables.worker_events_rows(problem),
        tables.WORKER_EVENT_COLUMNS,
        "node-events",
        {
            "worker": st.column_config.SelectboxColumn(
                options=[str(w["id"]) for w in workers if w.get("id")]
            ),
            "event": st.column_config.SelectboxColumn(options=list(tables.WORKER_EVENTS)),
        },
    )
with tab_settings:
    options, rev = policy_options(), st.session_state.revision
    names, modes, units = options["policies"], ["lenient", "strict"], ["minute", "hour", "second"]
    current = policy_in.get("name", names[0])
    policy = st.selectbox(
        "Политика", names, index=names.index(current) if current in names else 0, key=f"p{rev}"
    )
    aging = st.number_input(
        "Aging: через сколько единиц ожидания поднимать класс задачи (0 — выключено)",
        min_value=0,
        value=policy_in.get("aging", options["defaults"].get("aging")) or 0,
        key=f"a{rev}",
    )
    mode = st.radio(
        "Режим проверки",
        modes,
        index=modes.index(policy_in.get("validation_mode", "lenient")),
        horizontal=True,
        key=f"m{rev}",
    )
    time_unit = st.selectbox(
        "Единица времени", units, index=units.index(problem.get("time_unit", "minute")), key=f"u{rev}"
    )

settings = {"policy": policy, "aging": int(aging) or None, "validation_mode": mode, "time_unit": time_unit}
current_problem = tables.build_problem(workers, tasks, submits, node_events, settings)

with st.sidebar:
    st.download_button(
        "Скачать вход",
        json.dumps(current_problem, ensure_ascii=False, indent=2),
        "problem.json",
        "application/json",
        width="stretch",
    )


def run_policy(name: str) -> dict[str, Any] | None:
    """Прогон текущего входа политикой `name`; ошибки показываются и дают None."""
    status, body = client.run(current_problem, {**current_problem["policy"], "name": name})
    if status == 200:
        return dict(body)
    if status == 422:
        st.session_state.diagnostics = body["diagnostics"]
    else:
        st.error(body.get("error", f"Ошибка API: {status}"))
    return None


check, start, compare = st.columns([1, 1, 3])
try:
    if check.button("Проверить"):
        status, body = client.validate(current_problem, mode)
        if status == 413:
            st.error(f"Вход слишком большой: {body['error']}")
        else:
            st.session_state.diagnostics = body["diagnostics"]
    if start.button("Запустить", type="primary") and (outcome := run_policy(policy)):
        st.session_state.run = {**outcome, "problem": current_problem}
        st.session_state.diagnostics = outcome["result"]["diagnostics"]
    if compare.button("Сравнить политики"):
        found = {name: run_policy(name) for name in options["policies"]}
        st.session_state.comparison = {name: out["result"] for name, out in found.items() if out}
except ApiUnavailable:
    st.error("API недоступен: запустите `make api` и обновите страницу.")

diagnostics = st.session_state.get("diagnostics")
if diagnostics is not None:
    if not diagnostics:
        st.success("Вход корректен.")
    for d in diagnostics:
        show = st.error if d["level"] == "error" else st.warning
        show(f"**{d['code']}** — {d['hint']}\n\n`{json.dumps(d['detail'], ensure_ascii=False)}`")

with tab_graph:
    st.graphviz_chart(dag.dependency_dot(current_problem, dag.problem_tasks(diagnostics or [])))


KIND_COLORS = {
    "data": "#4c78a8",
    "train": "#9c6ade",
    "eval": "#54a24b",
    "infer": "#f58518",
    "other": "#8c8c8c",
}
LATE, SUBMITTED = "#e45756", "#f2cf5b"


def gantt_figure(bars: list[dict[str, Any]], lanes: list[str], down: list[dict[str, Any]]) -> go.Figure:
    """Полоса — попытка на дорожке узла, цвет — тип задачи; рамка красная — опоздала, жёлтая — срочная.

    Штриховка — работа прерванной попытки, потерянная после последнего чекпоинта; серое — узел недоступен.
    """
    figure = go.Figure()
    order = lanes[::-1]  # первая дорожка — сверху
    for outage in down:
        rows = [i for i, lane in enumerate(order) if lane.startswith(f"{outage['worker']} · ")]
        figure.add_shape(
            type="rect",
            x0=outage["from"],
            x1=outage["to"],
            y0=min(rows) - 0.5,
            y1=max(rows) + 0.5,
            fillcolor="rgba(128,128,128,0.35)",
            line_width=0,
            layer="below",
            label={"text": "недоступен", "font": {"size": 11}},
        )
    for kind in sorted({b["kind"] for b in bars}):
        group = [b for b in bars if b["kind"] == kind]
        figure.add_bar(
            name=kind,
            orientation="h",
            y=[b["lane"] for b in group],
            base=[b["start"] for b in group],
            x=[b["end"] - b["start"] for b in group],
            text=[b["task"] for b in group],
            customdata=[b["end"] for b in group],
            textposition="inside",
            insidetextanchor="middle",
            marker_color=KIND_COLORS.get(kind, KIND_COLORS["other"]),
            marker_line_color=[
                LATE if b["late"] else SUBMITTED if b["submitted"] else "rgba(0,0,0,0)" for b in group
            ],
            marker_line_width=3,
            hovertemplate="%{text}: %{base}–%{customdata}<extra></extra>",
        )
    lost = [b for b in bars if b["lost_from"] is not None and b["end"] > b["lost_from"]]
    if lost:
        figure.add_bar(
            name="потеряно",
            orientation="h",
            y=[b["lane"] for b in lost],
            base=[b["lost_from"] for b in lost],
            x=[b["end"] - b["lost_from"] for b in lost],
            customdata=[b["end"] for b in lost],
            text=[b["task"] for b in lost],
            textposition="none",  # имя уже на полосе попытки; здесь оно только для подсказки
            marker_color="rgba(40,40,40,0.55)",
            marker_pattern_shape="/",
            hovertemplate="%{text}: потеряно %{base}–%{customdata}<extra></extra>",
        )
    figure.update_yaxes(categoryorder="array", categoryarray=order, tickmode="array", tickvals=lanes)
    figure.update_layout(
        barmode="overlay", height=160 + 44 * len(lanes), xaxis_title="время", legend_title="тип задачи"
    )
    return figure


if run := st.session_state.get("run"):
    result = run["result"]
    st.subheader(f"Результат: {result['policy']} — {result['status']}")
    m = result["metrics"]
    if m is None:
        st.info("Режим strict нашёл ошибки во входе: прогон не выполнялся.")
    else:
        cols = st.columns(6)
        above = m["makespan"] - (m["lower_bound"] or m["makespan"])
        cols[0].metric("Makespan", m["makespan"], f"+{above} к нижней границе" if above else None, "inverse")
        cols[1].metric("Нижняя граница", m["lower_bound"])
        cols[2].metric("Пропуски дедлайнов", sum(m["deadline_misses"].values()))
        cols[3].metric("Загрузка GPU", m["utilization_gpu"])
        cols[4].metric("Потерянная работа, GPU·ч", m["lost_work_gpu"])
        cols[5].metric("Среднее ожидание", m["mean_wait"])
        if result["violations"]:
            st.error(
                "Проверка расписания нашла нарушения:\n\n" + "\n".join(f"- {v}" for v in result["violations"])
            )
        else:
            st.success("Проверка расписания: нарушений нет.")
        bars = results.gantt_bars(result, run["problem"])
        lanes = results.lane_order(bars, [w["id"] for w in run["problem"]["cluster"]["workers"]])
        down = results.outages(run["events"], m["makespan"])
        st.plotly_chart(gantt_figure(bars, lanes, down), width="stretch")
        st.dataframe(pd.DataFrame(results.task_rows(result)), width="stretch", hide_index=True)
        chosen = st.selectbox("Почему ждала задача", sorted(result["tasks"]))
        waits = results.wait_rows(result, chosen)
        if waits:
            st.dataframe(pd.DataFrame(waits), width="stretch", hide_index=True)
        else:
            st.caption("Задача не ждала.")
    left, right = st.columns(2)
    left.download_button(
        "Скачать result.json", json.dumps(result, ensure_ascii=False, indent=2), "result.json"
    )
    right.download_button("Скачать events.jsonl", results.events_jsonl(run["events"]), "events.jsonl")

if comparison := st.session_state.get("comparison"):
    st.subheader("Сравнение политик на текущем входе")
    st.dataframe(pd.DataFrame(results.comparison_rows(comparison)), width="stretch", hide_index=True)
