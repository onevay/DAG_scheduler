import type { comparisonRows } from "../run/results";

export type Row = ReturnType<typeof comparisonRows>[number];
export type MetricKey = "makespan" | "util" | "wait" | "lost";

interface Metric {
  label: string;
  title: (unit: string) => string;
  get: (r: Row) => number | null;
  format: (v: number, unit: string) => string;
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
    title: () => "Загрузка GPU",
    get: (r) => r.utilizationGpu,
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
    title: (u) => `Потерянная работа, GPU·${u}`,
    get: (r) => r.lostWorkGpu,
    format: (v, u) => `${v} GPU·${u}`,
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
export function capsules(rows: readonly Row[], key: MetricKey): Capsule[] {
  const metric = METRICS[key];
  const values = rows.map(metric.get).filter((v): v is number => v !== null);
  const max = Math.max(...values, 1e-9);
  const best = values.length ? (metric.better === "low" ? Math.min(...values) : Math.max(...values)) : null;
  return rows.map((r) => {
    const value = metric.get(r);
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
export function profile(rows: readonly Row[], policy: string): Record<MetricKey, number> {
  const row = rows.find((r) => r.policy === policy);
  const score = (key: MetricKey) => {
    const m = METRICS[key];
    const values = rows.map(m.get).filter((v): v is number => v !== null);
    const v = row ? m.get(row) : null;
    if (v === null || values.length === 0) return 0;
    if (m.better === "low") return v === 0 ? 1 : Math.min(...values) / v;
    const top = Math.max(...values);
    return top === 0 ? 1 : v / top;
  };
  return { makespan: score("makespan"), util: score("util"), wait: score("wait"), lost: score("lost") };
}

/** Одна фраза о том, что показывает метрика: кто лучше и насколько, или что разницы нет. */
export function insight(rows: readonly Row[], key: MetricKey, unit: string): string {
  const metric = METRICS[key];
  const known = rows.filter((r) => metric.get(r) !== null);
  if (known.length === 0) return "Ни один прогон не дошёл до расписания — метрик нет.";
  const sorted = [...known].sort((a, b) => {
    const d = (metric.get(a) ?? 0) - (metric.get(b) ?? 0);
    return metric.better === "low" ? d : -d;
  });
  const first = sorted[0] as Row;
  const last = sorted.at(-1) as Row;
  const fmt = (r: Row) => metric.format(metric.get(r) ?? 0, unit);
  if (fmt(first) === fmt(last))
    return `По метрике «${metric.label}» политики не различаются: у всех ${fmt(first)}.`;
  const winners = known.filter((r) => fmt(r) === fmt(first)).map((r) => r.policy);
  return `Лучше всех — ${winners.join(", ")} (${fmt(first)}), хуже всех — ${last.policy} (${fmt(last)}).`;
}
