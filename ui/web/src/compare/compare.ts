import type { comparisonRows } from "../run/results";
import { resourceLabel, sortResources, work } from "../ui/resources";

export type Row = ReturnType<typeof comparisonRows>[number];
export type MetricKey = "makespan" | "util" | "wait" | "lost";

/** Метрика сравнения; загрузка и потери — по ресурсу `res`, остальным он не нужен. */
interface Metric {
  label: string;
  title: (unit: string, res: string) => string;
  get: (r: Row, res: string) => number | null;
  format: (v: number, unit: string, res: string) => string;
  /** Что лучше: меньше или больше. */
  better: "low" | "high";
}

const round = (v: number) => String(Math.round(v * 100) / 100);

export const METRICS: Record<MetricKey, Metric> = {
  makespan: {
    label: "Makespan",
    title: (u) => `Длина расписания, ${u}`,
    get: (r) => r.makespan,
    format: (v, u) => `${v} ${u}`,
    better: "low",
  },
  util: {
    label: "Загрузка",
    title: (_, res) => `Загрузка ${resourceLabel(res)}`,
    get: (r, res) => r.utilization?.[res] ?? null,
    format: (v) => `${Math.round(v * 100)}%`,
    better: "high",
  },
  wait: {
    label: "Ожидание",
    title: (u) => `Среднее ожидание, ${u}`,
    get: (r) => r.meanWait,
    format: (v, u) => `${round(v)} ${u}`,
    better: "low",
  },
  lost: {
    label: "Потери",
    title: (_, res) => `Потерянная работа, ${resourceLabel(res)}`,
    get: (r, res) => r.lostWork?.[res] ?? null,
    format: (v, u, res) => work(res, v, u),
    better: "low",
  },
};

export interface Capsule {
  policy: string;
  value: number | null;
  /** Высота капсулы — доля от самой высокой, 0…1. */
  height: number;
  best: boolean;
}

/** Капсулы по метрике: высота пропорциональна значению, лучшее значение отмечено (их может быть несколько). */
export function capsules(rows: readonly Row[], key: MetricKey, res: string): Capsule[] {
  const metric = METRICS[key];
  const values = rows.map((r) => metric.get(r, res)).filter((v): v is number => v !== null);
  const max = Math.max(...values, 1e-9);
  const best = values.length ? (metric.better === "low" ? Math.min(...values) : Math.max(...values)) : null;
  return rows.map((r) => {
    const value = metric.get(r, res);
    return {
      policy: r.policy,
      value,
      height: value === null ? 0 : value / max,
      best: value !== null && value === best,
    };
  });
}

/**
 * Профиль политики: по каждой метрике доля от лучшей среди политик, 1 — лучшая. Для «меньше — лучше» это
 * лучшее / своё (ноль — всегда лучший), для «больше — лучше» — своё / лучшее.
 */
export function profile(rows: readonly Row[], policy: string, res: string): Record<MetricKey, number> {
  const row = rows.find((r) => r.policy === policy);
  const score = (key: MetricKey) => {
    const m = METRICS[key];
    const values = rows.map((r) => m.get(r, res)).filter((v): v is number => v !== null);
    const v = row ? m.get(row, res) : null;
    if (v === null || values.length === 0) return 0;
    if (m.better === "low") return v === 0 ? 1 : Math.min(...values) / v;
    const top = Math.max(...values);
    return top === 0 ? 1 : v / top;
  };
  return { makespan: score("makespan"), util: score("util"), wait: score("wait"), lost: score("lost") };
}

const OBJECTIVES: Record<NonNullable<Row["objective"]>, string> = {
  lexicographic: "сначала пропуски дедлайнов от старших классов, затем взвешенное опоздание, затем makespan",
  weighted: "взвешенное опоздание плюс makespan",
};

/** Ресурсы, по которым у прогонов есть загрузка, — для выбора в загрузке и потерях. */
export function rowResources(rows: readonly Row[]): string[] {
  return sortResources(rows.flatMap((r) => Object.keys(r.utilization ?? {})));
}

/** Сравнение оценок по цели поэлементно: меньше — лучше. */
export function compareScores(a: readonly number[], b: readonly number[]): number {
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    const d = (a[i] ?? 0) - (b[i] ?? 0);
    if (d !== 0) return d;
  }
  return 0;
}

export interface Place {
  policy: string;
  /** Место по цели: равные оценки делят место, следующее идёт через них (1, 1, 3). */
  place: number;
}

/** Места политик по цели политики; прогоны без метрик мест не получают. */
export function ranking(rows: readonly Row[]): Place[] {
  const scored = rows
    .flatMap((r) => (r.score ? [{ policy: r.policy, score: r.score }] : []))
    .sort((a, b) => compareScores(a.score, b.score) || a.policy.localeCompare(b.policy));
  return scored.map((r) => ({
    policy: r.policy,
    place: 1 + scored.findIndex((o) => compareScores(o.score, r.score) === 0),
  }));
}

/** Фраза о местах по цели: что считается лучшим и кто где. */
export function objectiveInsight(rows: readonly Row[]): string | null {
  const places = ranking(rows);
  const objective = rows.find((r) => r.objective)?.objective;
  if (places.length === 0 || !objective) return null;
  const groups = new Map<number, string[]>();
  for (const p of places) groups.set(p.place, [...(groups.get(p.place) ?? []), p.policy]);
  const list = [...groups].map(([place, names]) => `${place} — ${names.join(", ")}`).join("; ");
  return `По цели (${OBJECTIVES[objective]}): ${list}.`;
}

/** Одна фраза о том, что показывает метрика: кто лучше и насколько, или что разницы нет. */
export function insight(rows: readonly Row[], key: MetricKey, unit: string, res: string): string {
  const metric = METRICS[key];
  const get = (r: Row) => metric.get(r, res);
  const known = rows.filter((r) => get(r) !== null);
  if (known.length === 0) return "Ни один прогон не дошёл до расписания — метрик нет.";
  const sorted = [...known].sort((a, b) => {
    const d = (get(a) ?? 0) - (get(b) ?? 0);
    return metric.better === "low" ? d : -d;
  });
  const first = sorted[0] as Row;
  const last = sorted.at(-1) as Row;
  const fmt = (r: Row) => metric.format(get(r) ?? 0, unit, res);
  if (fmt(first) === fmt(last))
    return `По метрике «${metric.label}» политики не различаются: у всех ${fmt(first)}.`;
  const winners = known.filter((r) => fmt(r) === fmt(first)).map((r) => r.policy);
  return `Лучше всех — ${winners.join(", ")} (${fmt(first)}), хуже всех — ${last.policy} (${fmt(last)}).`;
}
