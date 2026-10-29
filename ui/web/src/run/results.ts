import type { JournalRecord, Problem, Result, TaskKind } from "../api/types";

export interface GanttBar {
  task: string;
  attempt: number;
  worker: string;
  start: number;
  end: number;
  /** С этого момента работа попытки потеряна: её прервали после последнего чекпоинта. */
  lostFrom: number | null;
  outcome: "DONE" | "INT" | "PRE" | null;
  kind: TaskKind;
  submitted: boolean;
  late: boolean;
  laneIndex: number;
  lane: string;
}

const laneName = (worker: string, index: number) => `${worker} · ${index + 1}`;
// Сравнение по кодам символов, как sorted() в Python: порядок не зависит от локали браузера
const cmp = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0);

/** Полосы попыток с дорожкой узла: задачи, идущие на узле одновременно, попадают на разные дорожки. */
export function ganttBars(result: Result, problem: Problem): GanttBar[] {
  const tasks = new Map(problem.tasks.map((t) => [t.id, t]));
  const submitted = new Map(
    (problem.events ?? []).flatMap((e) =>
      e.type === "submit_task" && e.task ? [[e.task.id, e.task] as const] : [],
    ),
  );
  const bars = result.attempts.map((a) => {
    const task = tasks.get(a.task) ?? submitted.get(a.task);
    const interrupted = a.outcome === "INT" || a.outcome === "PRE";
    return {
      task: a.task,
      attempt: a.attempt,
      worker: a.worker,
      start: a.start,
      end: a.end ?? a.start,
      lostFrom: interrupted ? a.start + a.saved : null,
      outcome: a.outcome ?? null,
      kind: task?.kind ?? "other",
      submitted: submitted.has(a.task),
      late: (result.tasks[a.task]?.tardiness ?? 0) > 0,
      laneIndex: 0,
      lane: "",
    };
  });
  return assignLanes(bars);
}

/** Жадная раскладка: попытка занимает первую дорожку узла, освободившуюся к её старту. */
export function assignLanes<B extends { worker: string; start: number; end: number; task: string }>(
  bars: B[],
): (B & { laneIndex: number; lane: string })[] {
  const ends = new Map<string, number[]>();
  const byStart = [...bars].sort(
    (a, b) => cmp(a.worker, b.worker) || a.start - b.start || cmp(a.task, b.task),
  );
  const placed = byStart.map((bar) => {
    const lanes = ends.get(bar.worker) ?? [];
    ends.set(bar.worker, lanes);
    let index = lanes.findIndex((end) => end <= bar.start);
    if (index === -1) index = lanes.push(bar.end) - 1;
    else lanes[index] = bar.end;
    return { ...bar, laneIndex: index, lane: laneName(bar.worker, index) };
  });
  return placed.sort((a, b) => cmp(a.worker, b.worker) || a.laneIndex - b.laneIndex || a.start - b.start);
}

/** Дорожки сверху вниз: по узлам, у каждого узла хотя бы одна — чтобы был виден и его простой. */
export function laneOrder(
  bars: readonly { worker: string; laneIndex: number }[],
  workers: readonly string[],
): string[] {
  const lanes = new Map<string, Set<number>>(workers.map((w) => [w, new Set([0])]));
  for (const b of bars) {
    const set = lanes.get(b.worker) ?? new Set<number>();
    set.add(b.laneIndex);
    lanes.set(b.worker, set);
  }
  return [...lanes.keys()]
    .sort()
    .flatMap((w) => [...(lanes.get(w) ?? [])].sort((a, b) => a - b).map((i) => laneName(w, i)));
}

export interface JournalOutage {
  worker: string;
  from: number;
  to: number;
}

/** Интервалы недоступности узлов из журнала; не вернувшийся узел недоступен до конца прогона. */
export function journalOutages(events: readonly JournalRecord[], endOfRun: number): JournalOutage[] {
  const out: JournalOutage[] = [];
  const since = new Map<string, number>();
  for (const r of events) {
    if (r.type === "WORKER_DOWN") since.set(r.worker, r.t);
    else if (r.type === "WORKER_UP") {
      const from = since.get(r.worker);
      if (from === undefined) continue;
      out.push({ worker: r.worker, from, to: r.t });
      since.delete(r.worker);
    }
  }
  const rest = [...since].sort(([a], [b]) => cmp(a, b));
  return [...out, ...rest.map(([worker, from]) => ({ worker, from, to: Math.max(from, endOfRun) }))];
}

/** Последний резерв каждой задачи: на каком узле и к какому моменту её обещали начать. */
export function reservations(events: readonly JournalRecord[]) {
  const last = new Map<string, Extract<JournalRecord, { type: "RESERVE" }>>();
  for (const r of events) if (r.type === "RESERVE") last.set(r.task, r);
  return [...last]
    .sort(([a], [b]) => cmp(a, b))
    .map(([task, r]) => ({ task, worker: r.worker, tRes: r.t_res, since: r.t }));
}

/** Перепланирования по строке: что случилось и как сдвинулись прогнозные старты. */
export function replanRows(replans: Result["replans"]) {
  const signed = (n: number) => (n > 0 ? `+${n}` : `${n}`);
  return (replans ?? []).map((r) => ({
    t: r.t,
    events: r.events.map((e) => `${e.type} ${e.task ?? e.worker ?? ""}`).join(", "),
    shifted: r.changes.map((c) => `${c.task} ${signed(c.delta)}`).join(", "),
    added: Object.entries(r.new_tasks)
      .map(([k, t]) => `${k} → ${t}`)
      .join(", "),
    dropped: r.dropped.join(", "),
  }));
}

/** Итог по задачам: сколько было попыток, сколько ждала и почему дольше всего. */
export function taskRows(result: Result) {
  const attempts = new Map<string, number>();
  for (const a of result.attempts) attempts.set(a.task, (attempts.get(a.task) ?? 0) + 1);
  return Object.entries(result.tasks).map(([task, t]) => {
    const totals = new Map<string, number>();
    for (const w of t.waits) totals.set(w.code, (totals.get(w.code) ?? 0) + w.to - w.from);
    // Главная причина — самое долгое ожидание; при равенстве — по коду, как в журнале
    const main = [...totals].sort(([ca, a], [cb, b]) => b - a || cmp(cb, ca))[0]?.[0] ?? "";
    return {
      task,
      status: t.status,
      attempts: attempts.get(task) ?? 0,
      start: t.start,
      finish: t.finish,
      deadline: t.deadline,
      tardiness: t.tardiness,
      waited: [...totals.values()].reduce((s, v) => s + v, 0),
      mainReason: main,
    };
  });
}

/** Сводка нескольких прогонов одного входа: по строке на политику. */
export function comparisonRows(results: Readonly<Record<string, Result>>) {
  return Object.entries(results).map(([policy, r]) => {
    const m = r.metrics;
    return {
      policy,
      status: r.status,
      makespan: m?.makespan ?? null,
      gapToLb: m?.gap_to_lb ?? null,
      deadlineMisses: m ? Object.values(m.deadline_misses).reduce((s, v) => s + v, 0) : null,
      weightedTardiness: m?.weighted_tardiness ?? null,
      utilization: m?.utilization ?? null,
      lostWork: m?.lost_work ?? null,
      meanWait: m?.mean_wait ?? null,
      objective: m?.score.objective ?? null,
      score: m?.score.key ?? null,
      violations: r.violations.length,
    };
  });
}

export interface EstimateRow {
  task: string;
  source: string;
  mean: number;
  p80: number;
  /** Наблюдённая работа: позиция старта законченной попытки плюс её время, умноженное на скорость узла. */
  actual: number | null;
  /** (факт − среднее) / среднее. */
  error: number | null;
}

/** Оценки задач без длительности против того, сколько работы они заняли на самом деле. */
export function estimateRows(result: Result, problem: Problem): EstimateRow[] {
  const speed = new Map(problem.cluster.workers.map((w) => [w.id, w.speed ?? 1]));
  const done = new Map(
    result.attempts.flatMap((a) =>
      a.outcome === "DONE" && a.end !== null
        ? [[a.task, a.resume_from + (a.end - a.start) * (speed.get(a.worker) ?? 1)] as const]
        : [],
    ),
  );
  return Object.entries(result.estimates ?? {})
    .sort(([a], [b]) => cmp(a, b))
    .map(([task, e]) => {
      const actual = done.get(task) ?? null;
      return {
        task,
        source: e.source,
        mean: e.mean,
        p80: e.p80,
        actual,
        error: actual === null || e.mean <= 0 ? null : (actual - e.mean) / e.mean,
      };
    });
}

/** Средняя абсолютная ошибка и сколько задач уложилось в квантиль 0,8. */
export function estimateSummary(rows: readonly EstimateRow[]) {
  const seen = rows.filter((r) => r.error !== null);
  const mean = seen.length ? seen.reduce((s, r) => s + Math.abs(r.error ?? 0), 0) / seen.length : null;
  const within = seen.filter((r) => (r.actual ?? 0) <= r.p80).length;
  return { mean, within, seen: seen.length };
}
