import type { Task } from "../api/types";
import type { Draft } from "../draft/draft";

export const NODE_WIDTH = 136;
export const NODE_HEIGHT = 60;
const COLUMN = NODE_WIDTH + 40;
const ROW = NODE_HEIGHT + 16;

export interface XY {
  x: number;
  y: number;
}

export interface GraphTask {
  task: Task;
  /** Подаётся событием во время прогона, а не лежит во входе с начала. */
  submitted: boolean;
}

export interface GraphEdge {
  id: string;
  from: string;
  to: string;
  critical: boolean;
  submitted: boolean;
}

/** Все задачи графа: из входа и поданные событиями `submit_task`. */
export function graphTasks(draft: Draft): GraphTask[] {
  const submitted = (draft.events ?? []).flatMap((e) =>
    e.type === "submit_task" && e.task ? [{ task: e.task, submitted: true }] : [],
  );
  return [...draft.tasks.map((task) => ({ task, submitted: false })), ...submitted];
}

/** Рёбра «зависимость → задача»; критические — соседние пары критического пути. */
export function graphEdges(tasks: readonly GraphTask[], criticalPath: readonly string[]): GraphEdge[] {
  const known = new Set(tasks.map((t) => t.task.id));
  const critical = new Set(criticalPath.slice(1).map((id, i) => `${criticalPath[i]}->${id}`));
  return tasks.flatMap(({ task, submitted }) =>
    [...new Set(task.dependencies ?? [])]
      .filter((dep) => known.has(dep))
      .map((dep) => {
        const id = `${dep}->${task.id}`;
        return { id, from: dep, to: task.id, critical: critical.has(id), submitted };
      }),
  );
}

/**
 * Слой задачи — длина самой длинной цепочки зависимостей до неё. Задачи в цикле получают конечный
 * слой: длина цепочки ограничена числом задач.
 */
export function layers(tasks: readonly Task[]): Map<string, number> {
  const ids = new Set(tasks.map((t) => t.id));
  const depth = new Map(tasks.map((t) => [t.id, 0]));
  for (let round = 0; round < tasks.length; round++) {
    let changed = false;
    for (const task of tasks) {
      for (const dep of task.dependencies ?? []) {
        if (!ids.has(dep) || dep === task.id) continue;
        const candidate = Math.min((depth.get(dep) ?? 0) + 1, tasks.length - 1);
        if (candidate > (depth.get(task.id) ?? 0)) {
          depth.set(task.id, candidate);
          changed = true;
        }
      }
    }
    if (!changed) break;
  }
  return depth;
}

/** Раскладка слева направо по слоям; внутри слоя — в порядке входа. */
export function layeredLayout(tasks: readonly Task[]): Map<string, XY> {
  const depth = layers(tasks);
  const filled = new Map<number, number>();
  const out = new Map<string, XY>();
  for (const task of tasks) {
    const column = depth.get(task.id) ?? 0;
    const row = filled.get(column) ?? 0;
    filled.set(column, row + 1);
    out.set(task.id, { x: column * COLUMN, y: row * ROW });
  }
  return out;
}
