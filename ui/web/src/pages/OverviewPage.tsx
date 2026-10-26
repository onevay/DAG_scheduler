import { useState } from "react";
import { Link } from "react-router";

import { usePolicies, useRun, useRunProblem, useRuns } from "../api/hooks";
import type { Problem, Result, RunListItem } from "../api/types";
import { useDraft } from "../draft/DraftContext";
import { gapLabel, hourMatrix, nodeLoad } from "../overview/load";
import { NewScenario } from "../overview/NewScenario";
import styles from "../overview/Overview.module.css";
import { MetricCard, PillMeter } from "../ui/data";
import { NODES, plural, TASKS } from "../ui/plural";

const UNITS = { hour: "ч", minute: "мин", second: "с" } as const;

/** Обзор: сохранённые прогоны, метрики и загрузка узлов выбранного, начало нового сценария. */
export function OverviewPage() {
  const runs = useRuns();
  const [picked, setPicked] = useState<string | null>(null);
  const list = runs.data ?? [];
  const selected = picked ?? list[0]?.run_id ?? null;

  return (
    <>
      <header className={`${styles.hero} motion-rise`}>
        <h1 className={styles.title}>
          Планируйте <span className={styles.bubble} aria-hidden="true" /> эксперименты и{" "}
          <span className={`${styles.bubble} ${styles.accent}`} aria-hidden="true" /> кластер
        </h1>
      </header>
      <div className={styles.layout}>
        <div className={styles.main}>{selected ? <Selected runId={selected} /> : <NewScenario wide />}</div>
        <aside className={styles.aside} aria-label="Прогоны и разделы">
          <QuickLinks />
          <section aria-labelledby="overview-runs" className={styles.runs}>
            <div className={styles.runsHead}>
              <h2 id="overview-runs" className={styles.h3}>
                Прогоны
              </h2>
              <span className={styles.muted}>сохранено {list.length}</span>
            </div>
            {runs.isError && <p className={styles.muted}>Список не загрузился: {runs.error.message}</p>}
            {runs.data && list.length === 0 && (
              <p className={styles.muted}>Прогонов пока нет — запустите пример.</p>
            )}
            {list.map((r) => (
              <RunRow key={r.run_id} run={r} on={r.run_id === selected} onPick={() => setPicked(r.run_id)} />
            ))}
          </section>
        </aside>
      </div>
    </>
  );
}

function RunRow({ run, on, onPick }: { run: RunListItem; on: boolean; onPick: () => void }) {
  return (
    <button type="button" className={styles.row} aria-pressed={on} onClick={onPick}>
      <span className={styles.rowIcon} data-ok={run.status === "OK"} aria-hidden="true" />
      <span className={styles.rowText}>
        <span className={styles.rowName}>{run.name ?? run.run_id}</span>
        <span className={styles.muted}>
          {run.policy} · {plural(run.tasks, TASKS)} · {time(run.created_at)}
        </span>
      </span>
      <span className={styles.rowValue}>{run.makespan ?? run.status}</span>
    </button>
  );
}

function QuickLinks() {
  const { draft } = useDraft();
  const policies = usePolicies();
  const gpu = draft.cluster.workers.reduce((s, w) => s + (w.resources.gpu ?? 0), 0);
  return (
    <div className={styles.quick}>
      <Link to="/cluster" className={`${styles.tile} motion-lift`}>
        <span className={styles.tileTitle}>Кластер</span>
        <span className={styles.muted}>
          {plural(draft.cluster.workers.length, NODES)} · {gpu} GPU
        </span>
      </Link>
      <Link to="/compare" className={`${styles.tile} motion-lift`}>
        <span className={styles.tileTitle}>Политики</span>
        <span className={styles.muted}>
          {policies.data ? `${policies.data.policies.length} доступны` : "…"}
        </span>
      </Link>
    </div>
  );
}

function Selected({ runId }: { runId: string }) {
  const run = useRun(runId);
  const problem = useRunProblem(runId);
  if (run.isError || problem.isError) return <p className={styles.muted}>Прогон не загрузился.</p>;
  if (!run.data || !problem.data) return <p className={styles.muted}>Загружаю прогон {runId}…</p>;
  return <RunOverview runId={runId} result={run.data.result} problem={problem.data} />;
}

function RunOverview({ runId, result, problem }: { runId: string; result: Result; problem: Problem }) {
  const m = result.metrics;
  const unit = UNITS[problem.time_unit ?? "hour"];
  if (!m)
    return (
      <p className={styles.muted}>
        Прогон {runId} не дошёл до расписания ({result.status}).
      </p>
    );
  const gap = gapLabel(m.makespan, m.lower_bound);
  const util = m.utilization.gpu ?? 0;
  const misses = Object.values(m.deadline_misses).reduce((s, v) => s + v, 0);

  return (
    <>
      <div className={styles.cards}>
        <MetricCard label="Makespan" value={m.makespan} unit={unit} tone="surface">
          {gap && (
            <span className={styles.gap}>
              {gap} · граница {m.lower_bound}
            </span>
          )}
          <PillMeter share={m.lower_bound ? m.lower_bound / m.makespan : 1} />
        </MetricCard>
        <MetricCard label="Загрузка GPU" value={Math.round(util * 100)} unit="%" tone="accent">
          <span className={styles.gap}>
            пропущено дедлайнов {misses} · потеряно {m.lost_work.gpu ?? 0} GPU·{unit}
          </span>
          <PillMeter share={util} tone="accent" />
        </MetricCard>
        <NewScenario />
      </div>
      <Nodes result={result} problem={problem} horizon={m.makespan} unit={unit} />
      <Link to={`/runs/${runId}`} className={`${styles.open} motion-lift`}>
        <span className={`${styles.live} motion-pulse`} aria-hidden="true" />
        <span className={styles.rowText}>
          <span className={styles.rowName}>Открыть расписание</span>
          <span className={styles.muted}>Гант, ожидания, перепланирования</span>
        </span>
      </Link>
    </>
  );
}

const STATES = { busy: "занят", lost: "потеряно", down: "недоступен", idle: "простой" } as const;

function Nodes({
  result,
  problem,
  horizon,
  unit,
}: {
  result: Result;
  problem: Problem;
  horizon: number;
  unit: string;
}) {
  const load = nodeLoad(result, problem, horizon);
  const matrix = hourMatrix(result, problem, horizon);
  return (
    <section aria-labelledby="overview-nodes" className={styles.nodes}>
      <div className={styles.nodesHead}>
        <h2 id="overview-nodes" className={styles.h2}>
          Узлы по часам
        </h2>
        <span className={styles.legend} aria-hidden="true">
          {(Object.keys(STATES) as (keyof typeof STATES)[]).map((s) => (
            <span key={s}>
              <span className={styles.dot} data-state={s} />
              {STATES[s]}
            </span>
          ))}
        </span>
        <span className={styles.chip}>
          {problem.name ?? "прогон"} · {result.policy}
        </span>
      </div>
      <div className={styles.nodesBody}>
        <div className={styles.columns}>
          {load.map((n) => (
            <div key={n.worker} className={styles.column}>
              <div className={styles.tube} aria-hidden="true">
                <span className={`${styles.down} motion-data`} style={{ height: `${n.down * 100}%` }} />
                <span className={`${styles.busy} motion-data`} style={{ height: `${n.busy * 100}%` }} />
              </div>
              <span className={styles.percent}>{Math.round(n.busy * 100)}%</span>
              <span className={styles.worker}>{n.worker}</span>
            </div>
          ))}
        </div>
        <div className={styles.matrix}>
          <div className={styles.scale} aria-hidden="true">
            <span>0 {unit}</span>
            <span>
              {horizon} {unit}
            </span>
          </div>
          {matrix.map((row) => (
            <div key={row.worker} className={styles.matrixRow}>
              <span className={styles.worker}>{row.worker}</span>
              <div
                className={styles.dots}
                role="img"
                aria-label={`${row.worker} по часам: ${summary(row.hours)}`}
              >
                {row.hours.map((s, h) => (
                  <span
                    key={h}
                    className={styles.dot}
                    data-state={s}
                    title={`${h}–${h + 1} ${unit}: ${STATES[s]}`}
                  />
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

/** «занят 12, простой 8, недоступен 2» — для читалок экрана вместо точек. */
function summary(hours: readonly (keyof typeof STATES)[]): string {
  const counts = new Map<string, number>();
  for (const h of hours) counts.set(h, (counts.get(h) ?? 0) + 1);
  return [...counts].map(([s, n]) => `${STATES[s as keyof typeof STATES]} ${n}`).join(", ");
}

/** Время создания прогона: одноимённые прогоны (варианты, события) различаются по нему. */
function time(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime())
    ? ""
    : date.toLocaleString("ru", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}
