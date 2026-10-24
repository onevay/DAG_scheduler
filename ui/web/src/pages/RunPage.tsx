import { useParams } from "react-router";

import { ApiError } from "../api/client";
import { useRun, useRunProblem } from "../api/hooks";
import type { Problem, Result } from "../api/types";
import { taskRows } from "../run/results";
import { Schedule } from "../run/Schedule";
import { ButtonLink } from "../ui/controls";
import { StatusChip } from "../ui/data";
import { PageHeader } from "../ui/PageHeader";
import styles from "./RunPage.module.css";

const UNITS = { hour: "ч", minute: "мин", second: "с" } as const;

export function RunPage() {
  const { runId = "" } = useParams();
  const run = useRun(runId);
  const problem = useRunProblem(runId);

  if (run.isError || problem.isError) {
    const error = run.error ?? problem.error;
    const missing = error instanceof ApiError && error.status === 404;
    return (
      <PageHeader title={missing ? "Прогон не найден" : "Прогон не загрузился"}>
        {missing ? `Прогона ${runId} нет в хранилище — запустите вход заново.` : error?.message}
      </PageHeader>
    );
  }
  if (!run.data || !problem.data) return <PageHeader title="Прогон">Загружаю прогон {runId}…</PageHeader>;

  const { result } = run.data;
  const unit = UNITS[problem.data.time_unit ?? "hour"];
  return (
    <>
      <RunHeader runId={runId} result={result} problem={problem.data} />
      <Metrics result={result} unit={unit} />
      <Schedule key={runId} runId={runId} result={result} problem={problem.data} unit={unit} />
      <TaskTable result={result} />
    </>
  );
}

function RunHeader({ runId, result, problem }: { runId: string; result: Result; problem: Problem }) {
  const m = result.metrics;
  const ok = result.status === "OK" && result.violations.length === 0;
  const status =
    result.violations.length > 0
      ? `${result.status} · нарушений: ${result.violations.length}`
      : `${result.status}${result.status === "OK" ? " · нарушений нет" : ""}`;
  const why =
    m && m.lower_bound != null && m.makespan > m.lower_bound
      ? `Почему ${m.makespan}, а не ${m.lower_bound}?`
      : "Объяснения";
  return (
    <PageHeader
      title={problem.name ?? "Прогон"}
      badge={
        <span className={styles.crumb}>
          Прогон <code>{runId}</code> · сохранён
        </span>
      }
      actions={
        <div className={styles.actions}>
          <ButtonLink to={`/explain/${runId}`} variant="secondary">
            {why}
          </ButtonLink>
          <ButtonLink to="/compare">Сравнить политики</ButtonLink>
        </div>
      }
    >
      <span className={styles.chips}>
        <span className={styles.chip}>{result.policy}</span>
        <span className={styles.chip} data-ok={ok}>
          <span className={styles.dot} aria-hidden="true" />
          {status}
        </span>
      </span>
    </PageHeader>
  );
}

function Metrics({ result, unit }: { result: Result; unit: string }) {
  const m = result.metrics;
  if (!m) return <p className={styles.note}>Метрик нет: прогон не дошёл до расписания ({result.status}).</p>;
  const misses = Object.values(m.deadline_misses).reduce((s, v) => s + v, 0);
  const cards = [
    {
      label: "Makespan",
      value: m.makespan,
      unit: m.lower_bound != null ? `/ ${m.lower_bound} ${unit}` : unit,
    },
    {
      label: "Загрузка GPU",
      value: m.utilization_gpu != null ? Math.round(m.utilization_gpu * 100) : "—",
      unit: "%",
      accent: true,
    },
    { label: "Потеряно", value: m.lost_work_gpu, unit: `GPU·${unit}` },
    { label: "Дедлайны", value: misses, unit: "пропущено" },
    { label: "Среднее ожидание", value: Number(m.mean_wait.toFixed(2)), unit },
  ];
  return (
    <div className={styles.metrics}>
      {cards.map((c) => (
        <div key={c.label} className={`${styles.metric} motion-lift`} data-accent={c.accent ?? false}>
          <span className={styles.metricLabel}>{c.label}</span>
          <span className={styles.metricValue}>
            {c.value} <span className={styles.metricUnit}>{c.unit}</span>
          </span>
        </div>
      ))}
    </div>
  );
}

function TaskTable({ result }: { result: Result }) {
  const rows = taskRows(result);
  const dash = (v: number | null) => v ?? "—";
  return (
    <section aria-labelledby="run-tasks" className={styles.section}>
      <h2 id="run-tasks" className={styles.h2}>
        Задачи
      </h2>
      <div className={styles.scroll}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th scope="col">Задача</th>
              <th scope="col">Статус</th>
              <th scope="col">Попыток</th>
              <th scope="col">Старт</th>
              <th scope="col">Финиш</th>
              <th scope="col">Дедлайн</th>
              <th scope="col">Опоздание</th>
              <th scope="col">Ждала</th>
              <th scope="col">Главная причина</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.task}>
                <th scope="row">
                  <code>{r.task}</code>
                </th>
                <td>
                  <StatusChip status={r.status} />
                </td>
                <td>{r.attempts}</td>
                <td>{dash(r.start)}</td>
                <td>{dash(r.finish)}</td>
                <td>{dash(r.deadline)}</td>
                <td>{dash(r.tardiness)}</td>
                <td>{r.waited}</td>
                <td>
                  <code>{r.mainReason || "—"}</code>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
