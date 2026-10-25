import type { InputEvent } from "../api/types";

export interface Outage {
  worker: string;
  from: number;
  to: number;
}

/** Интервалы недоступности узлов по событиям входа; не вернувшийся узел недоступен до `end`. */
export function outages(events: readonly InputEvent[], end: number): Outage[] {
  const out: Outage[] = [];
  const since = new Map<string, number>();
  for (const e of [...events].sort((a, b) => a.time - b.time)) {
    if (!e.worker) continue;
    // Повторный отказ лежащего узла ничего не меняет: интервал начинается с первого
    if (e.type === "worker_down" && !since.has(e.worker)) since.set(e.worker, e.time);
    const from = since.get(e.worker);
    if (e.type === "worker_up" && from !== undefined) {
      out.push({ worker: e.worker, from, to: e.time });
      since.delete(e.worker);
    }
  }
  for (const [worker, from] of since) out.push({ worker, from, to: Math.max(from, end) });
  return out;
}

/** Длина шкалы: с запасом за нижней границей makespan и последним событием. */
export function horizonOf(events: readonly InputEvent[], lowerBound: number | undefined): number {
  const last = Math.max(0, ...events.map((e) => e.time));
  return Math.max(4, Math.ceil(Math.max(lowerBound ?? 0, last) * 1.2));
}

/** Деления шкалы с «круглым» шагом 1, 2, 5, 10, 20, … — не больше `max` штук. */
export function ticks(horizon: number, max = 12): number[] {
  const step =
    [1, 2, 5]
      .flatMap((m) => [1, 10, 100, 1000].map((p) => m * p))
      .sort((a, b) => a - b)
      .find((s) => horizon / s <= max) ?? horizon;
  return Array.from({ length: Math.floor(horizon / step) + 1 }, (_, i) => i * step);
}

/** Окно отказа по умолчанию для «Уронить»: вторая четверть шкалы, не короче одной единицы времени. */
export function defaultOutage(horizon: number): { from: number; to: number } {
  const from = Math.max(1, Math.round(horizon / 4));
  return { from, to: from + Math.max(1, Math.round(horizon / 4)) };
}
