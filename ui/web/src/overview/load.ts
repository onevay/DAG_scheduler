import type { Problem, Result } from "../api/types";
import { ganttBars } from "../run/results";

export type HourState = "busy" | "lost" | "down" | "idle";

/**
 * Состояние каждого узла по часам: занят полезной работой, занят работой, которую потом потеряли, недоступен
 * или простаивает. Занятость — из попыток, недоступность — из простоя с кодом IDLE_DOWN.
 */
export function hourMatrix(
  result: Result,
  problem: Problem,
  horizon: number,
): { worker: string; hours: HourState[] }[] {
  const bars = ganttBars(result, problem);
  const down = (result.explain?.idle.intervals ?? []).filter((i) => i.code === "IDLE_DOWN");
  return problem.cluster.workers.map(({ id }) => {
    const mine = bars.filter((b) => b.worker === id);
    const hours = Array.from({ length: horizon }, (_, h): HourState => {
      const bar = mine.find((b) => b.start <= h && h < b.end);
      if (bar) return bar.lostFrom !== null && h >= bar.lostFrom ? "lost" : "busy";
      return down.some((i) => i.worker === id && i.from <= h && h < i.to) ? "down" : "idle";
    });
    return { worker: id, hours };
  });
}

export interface NodeLoad {
  worker: string;
  /** Доля GPU·часов узла, занятых попытками, 0…1. */
  busy: number;
  /** Доля времени, когда узел лежал, 0…1. */
  down: number;
}

/** Загрузка каждого узла за прогон: GPU·часы попыток на нём к его ёмкости за makespan. */
export function nodeLoad(result: Result, problem: Problem, horizon: number): NodeLoad[] {
  const gpuOf = new Map(problem.tasks.map((t) => [t.id, t.resources?.gpu ?? 0]));
  for (const e of problem.events ?? []) if (e.task) gpuOf.set(e.task.id, e.task.resources?.gpu ?? 0);
  const idle = result.explain?.idle.intervals ?? [];
  return problem.cluster.workers.map(({ id, resources }) => {
    const capacity = (resources.gpu ?? 0) * horizon;
    const used = result.attempts
      .filter((a) => a.worker === id)
      .reduce((s, a) => s + ((a.end ?? a.start) - a.start) * (gpuOf.get(a.task) ?? 0), 0);
    const downTime = idle
      .filter((i) => i.worker === id && i.code === "IDLE_DOWN")
      .reduce((s, i) => s + i.to - i.from, 0);
    return {
      worker: id,
      busy: capacity > 0 ? Math.min(1, used / capacity) : 0,
      down: horizon > 0 ? Math.min(1, downTime / horizon) : 0,
    };
  });
}

/** «= граница» или «+4.8%» — насколько makespan длиннее нижней границы. */
export function gapLabel(makespan: number, lowerBound: number | null | undefined): string | null {
  if (lowerBound == null || lowerBound <= 0) return null;
  if (makespan <= lowerBound) return "= граница";
  return `+${Math.round(((makespan - lowerBound) / lowerBound) * 1000) / 10}%`;
}
