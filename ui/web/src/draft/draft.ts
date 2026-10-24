import type { Diagnostic, InputEvent, Policy, Problem, Task, Worker } from "../api/types";

/** Черновик входа — это и есть `problem.json`: что собрано, то и уходит в API. */
export type Draft = Problem;

export const EMPTY_DRAFT: Draft = { tasks: [], cluster: { workers: [] } };

export type DraftAction =
  | { type: "replace"; draft: Draft }
  | { type: "setName"; name: string }
  | { type: "addTask"; task?: Partial<Task> }
  | { type: "updateTask"; id: string; patch: Partial<Task> }
  | { type: "removeTask"; id: string }
  | { type: "link"; from: string; to: string }
  | { type: "unlink"; from: string; to: string }
  | { type: "addWorker"; worker?: Partial<Worker> }
  | { type: "updateWorker"; id: string; patch: Partial<Worker> }
  | { type: "removeWorker"; id: string }
  | { type: "addEvent"; event: InputEvent }
  | { type: "updateEvent"; index: number; patch: Partial<InputEvent> }
  | { type: "removeEvent"; index: number }
  | { type: "setPolicy"; patch: Partial<Policy> };

/** Первый свободный id вида `prefix_N`. */
export function freeId(prefix: string, taken: Iterable<string>): string {
  const used = new Set(taken);
  let n = 1;
  while (used.has(`${prefix}_${n}`)) n += 1;
  return `${prefix}_${n}`;
}

const mapTasks = (draft: Draft, f: (task: Task) => Task): Draft => ({ ...draft, tasks: draft.tasks.map(f) });
const mapWorkers = (draft: Draft, f: (worker: Worker) => Worker): Draft => ({
  ...draft,
  cluster: { ...draft.cluster, workers: draft.cluster.workers.map(f) },
});

export function draftReducer(draft: Draft, action: DraftAction): Draft {
  switch (action.type) {
    case "replace":
      return action.draft;
    case "setName":
      return { ...draft, name: action.name || null };
    case "addTask": {
      const id =
        action.task?.id ??
        freeId(
          "task",
          draft.tasks.map((t) => t.id),
        );
      return {
        ...draft,
        tasks: [...draft.tasks, { duration: 1, resources: { gpu: 1 }, ...action.task, id }],
      };
    }
    case "updateTask": {
      const renamed = action.patch.id !== undefined && action.patch.id !== action.id ? action.patch.id : null;
      return mapTasks(draft, (task) => {
        const next = task.id === action.id ? { ...task, ...action.patch } : task;
        // Переименование тянет за собой ссылки на задачу в зависимостях
        return renamed && next.dependencies?.includes(action.id)
          ? { ...next, dependencies: next.dependencies.map((d) => (d === action.id ? renamed : d)) }
          : next;
      });
    }
    case "removeTask":
      return mapTasks({ ...draft, tasks: draft.tasks.filter((t) => t.id !== action.id) }, (task) =>
        task.dependencies?.includes(action.id)
          ? { ...task, dependencies: task.dependencies.filter((d) => d !== action.id) }
          : task,
      );
    case "link":
      if (action.from === action.to) return draft;
      return mapTasks(draft, (task) =>
        task.id === action.to && !task.dependencies?.includes(action.from)
          ? { ...task, dependencies: [...(task.dependencies ?? []), action.from] }
          : task,
      );
    case "unlink":
      return mapTasks(draft, (task) =>
        task.id === action.to
          ? { ...task, dependencies: task.dependencies?.filter((d) => d !== action.from) }
          : task,
      );
    case "addWorker": {
      const id =
        action.worker?.id ??
        freeId(
          "node",
          draft.cluster.workers.map((w) => w.id),
        );
      const worker = { resources: { gpu: 4 }, ...action.worker, id };
      return { ...draft, cluster: { ...draft.cluster, workers: [...draft.cluster.workers, worker] } };
    }
    case "updateWorker":
      return mapWorkers(draft, (w) => (w.id === action.id ? { ...w, ...action.patch } : w));
    case "removeWorker": {
      // Вместе с узлом уходят его отказы и возвраты: без узла событие некорректно
      const events = draft.events?.filter((e) => e.worker !== action.id);
      const workers = draft.cluster.workers.filter((w) => w.id !== action.id);
      return { ...draft, events, cluster: { ...draft.cluster, workers } };
    }
    case "addEvent": {
      // Устойчивая сортировка по времени: события одного момента остаются в порядке добавления
      const events = [...(draft.events ?? []), action.event].sort((a, b) => a.time - b.time);
      return { ...draft, events };
    }
    case "updateEvent": {
      const events = (draft.events ?? []).map((e, i) => (i === action.index ? { ...e, ...action.patch } : e));
      return { ...draft, events: events.sort((a, b) => a.time - b.time) };
    }
    case "removeEvent":
      return { ...draft, events: draft.events?.filter((_, i) => i !== action.index) };
    case "setPolicy":
      return { ...draft, policy: { ...draft.policy, ...action.patch } };
  }
}

/** Разбор `problem.json`: проверяется только форма, достаточная для редактора; остальное проверит API. */
export function parseDraft(text: string): Draft {
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch {
    throw new Error("Файл не в формате JSON");
  }
  if (!isDraft(data)) throw new Error("Это не problem.json: нужны tasks и cluster.workers");
  return data;
}

function isDraft(data: unknown): data is Draft {
  if (typeof data !== "object" || data === null) return false;
  const { tasks, cluster } = data as { tasks?: unknown; cluster?: { workers?: unknown } };
  return Array.isArray(tasks) && Array.isArray(cluster?.workers);
}

export function serializeDraft(draft: Draft): string {
  return JSON.stringify(draft, null, 2) + "\n";
}

const STORAGE_KEY = "expsched.draft.v1";

/** Черновик из localStorage; если его нет или он испорчен — пустой. */
export function loadDraft(storage: Storage | undefined = globalThis.localStorage): Draft {
  try {
    const text = storage?.getItem(STORAGE_KEY);
    return text ? parseDraft(text) : EMPTY_DRAFT;
  } catch {
    return EMPTY_DRAFT;
  }
}

export function saveDraft(draft: Draft, storage: Storage | undefined = globalThis.localStorage): void {
  try {
    storage?.setItem(STORAGE_KEY, JSON.stringify(draft));
  } catch {
    // Хранилище недоступно (приватный режим, квота): черновик живёт до перезагрузки
  }
}

/** Диагностики по задачам; `""` — общие для всего входа. */
export function diagnosticsByTask(
  draft: Draft,
  diagnostics: readonly Diagnostic[],
): Map<string, Diagnostic[]> {
  const out = new Map<string, Diagnostic[]>();
  const add = (task: string, d: Diagnostic) => out.set(task, [...(out.get(task) ?? []), d]);
  for (const d of diagnostics) {
    const tasks = tasksOf(draft, d.detail);
    if (tasks.length === 0) add("", d);
    for (const task of tasks) add(task, d);
  }
  return out;
}

function tasksOf(draft: Draft, detail: Record<string, unknown>): string[] {
  if (typeof detail.task === "string") return [detail.task];
  // Цикл приходит замкнутым (a → b → a): каждая задача — один раз
  if (Array.isArray(detail.cycle))
    return [...new Set(detail.cycle.filter((t): t is string => typeof t === "string"))];
  // Ошибка схемы указывает на поле: «tasks.3.duration» — четвёртая задача
  const index = typeof detail.loc === "string" ? /^tasks\.(\d+)/.exec(detail.loc)?.[1] : undefined;
  const task = index === undefined ? undefined : draft.tasks[Number(index)];
  return task ? [task.id] : [];
}
