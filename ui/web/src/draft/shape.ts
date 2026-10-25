/**
 * Проверка формы черновика — ровно то, без чего редактор упадёт: строки, числа, массивы на своих местах.
 * Смысл (циклы, ёмкость, ссылки) проверяет API; здесь — чтобы испорченный файл не ломал экраны.
 */

type Fields = Record<string, unknown>;

const isObject = (v: unknown): v is Fields => typeof v === "object" && v !== null && !Array.isArray(v);
const isNumber = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const isText = (v: unknown): v is string => typeof v === "string" && v !== "";
const isMap = (v: unknown, values: (x: unknown) => boolean) => isObject(v) && Object.values(v).every(values);
const optional = (v: unknown, check: (x: unknown) => boolean) => v === undefined || v === null || check(v);

const EVENT_TYPES = new Set(["worker_down", "worker_up", "submit_task"]);

function taskIssue(t: unknown): string | null {
  if (!isObject(t)) return "не объект";
  if (!isText(t.id)) return "нет id";
  if (!isNumber(t.duration)) return "duration — не число";
  const checks: [string, boolean][] = [
    ["kind", optional(t.kind, isText)],
    ["resources", optional(t.resources, (r) => isMap(r, isNumber))],
    ["dependencies", optional(t.dependencies, (d) => Array.isArray(d) && d.every(isText))],
    ["constraints", optional(t.constraints, (c) => isMap(c, isText))],
    ["priority", optional(t.priority, isNumber)],
    ["deadline", optional(t.deadline, isNumber)],
    ["release_time", optional(t.release_time, isNumber)],
    ["checkpoint_interval", optional(t.checkpoint_interval, isNumber)],
    ["max_retries", optional(t.max_retries, isNumber)],
    ["preemptible", optional(t.preemptible, (p) => typeof p === "boolean")],
  ];
  const bad = checks.find(([, ok]) => !ok);
  return bad ? `неверное поле ${bad[0]}` : null;
}

function workerIssue(w: unknown): string | null {
  if (!isObject(w)) return "не объект";
  if (!isText(w.id)) return "нет id";
  if (!isMap(w.resources, isNumber)) return "resources — не набор чисел";
  if (!optional(w.labels, (l) => isMap(l, isText))) return "labels — не набор строк";
  return null;
}

function eventIssue(e: unknown): string | null {
  if (!isObject(e)) return "не объект";
  if (!isNumber(e.time)) return "time — не число";
  if (!isText(e.type) || !EVENT_TYPES.has(e.type)) return `неизвестный тип ${String(e.type)}`;
  if (e.type === "submit_task") {
    const issue = taskIssue(e.task);
    return issue && `задача события: ${issue}`;
  }
  return isText(e.worker) ? null : "нет worker";
}

const label = (item: unknown, index: number) =>
  isObject(item) && isText(item.id) ? `${index + 1} (${item.id})` : `${index + 1}`;

/** Первая найденная неполадка формы по-русски или null, если черновик можно открыть. */
export function draftIssue(data: unknown): string | null {
  if (!isObject(data)) return "это не объект JSON";
  const { tasks, cluster, events } = data;
  if (!Array.isArray(tasks)) return "нет списка tasks";
  if (!isObject(cluster) || !Array.isArray(cluster.workers)) return "нет списка cluster.workers";
  for (const [i, t] of tasks.entries()) {
    const issue = taskIssue(t);
    if (issue) return `задача ${label(t, i)}: ${issue}`;
  }
  for (const [i, w] of cluster.workers.entries()) {
    const issue = workerIssue(w);
    if (issue) return `узел ${label(w, i)}: ${issue}`;
  }
  if (!optional(events, Array.isArray)) return "events — не список";
  for (const [i, e] of ((events as unknown[] | undefined) ?? []).entries()) {
    const issue = eventIssue(e);
    if (issue) return `событие ${i + 1}: ${issue}`;
  }
  return null;
}
