import type { Intervention, Problem, WhatIf } from "../api/types";
import { freeId } from "../draft/draft";

export type Kind = Intervention["kind"];
type Delta = NonNullable<WhatIf["variants"][number]["delta"]>;

export const KINDS: readonly { value: Kind; label: string }[] = [
  { value: "add_worker", label: "добавить узел" },
  { value: "set_checkpoint", label: "чекпоинт задачи" },
  { value: "set_preemptible", label: "вытесняемость" },
  { value: "set_priority", label: "приоритет" },
  { value: "set_deadline", label: "дедлайн" },
  { value: "remove_failures", label: "убрать отказы узла" },
];

/** Вмешательство одной фразой — заголовок карточки. */
export function describe(i: Intervention, unit: string): string {
  switch (i.kind) {
    case "add_worker":
      return `Ещё узел ${i.worker.id} на ${i.worker.resources.gpu ?? 0} GPU`;
    case "set_checkpoint":
      return i.checkpoint_interval
        ? `Чекпоинт ${i.task} каждые ${i.checkpoint_interval} ${unit}`
        : `${i.task} без чекпоинтов`;
    case "set_preemptible":
      return i.preemptible === false ? `${i.task} нельзя вытеснять` : `${i.task} можно вытеснять`;
    case "set_priority":
      return `Приоритет ${i.task} — ${i.priority}`;
    case "set_deadline":
      return i.deadline == null ? `${i.task} без дедлайна` : `Дедлайн ${i.task} — ${i.deadline} ${unit}`;
    case "remove_failures":
      return `Без отказов ${i.worker}`;
  }
}

/**
 * Вмешательства, которые стоит проверить первыми: убрать каждый отказ, добавить узел как самый крупный,
 * чекпоинт самой длинной задачи без него. Не больше четырёх.
 */
export function suggest(problem: Problem): Intervention[] {
  const out: Intervention[] = [];
  const failing = [
    ...new Set((problem.events ?? []).filter((e) => e.type === "worker_down").map((e) => e.worker)),
  ];
  for (const worker of failing) if (worker) out.push({ kind: "remove_failures", worker });
  const workers = problem.cluster.workers;
  if (workers.length > 0) {
    const gpu = Math.max(...workers.map((w) => w.resources.gpu ?? 0));
    const id = freeId(
      "node",
      workers.map((w) => w.id),
    );
    out.push({ kind: "add_worker", worker: { id, resources: { gpu } } });
  }
  const longest = [...problem.tasks]
    .filter((t) => !t.checkpoint_interval && t.duration > 1)
    .sort((a, b) => b.duration - a.duration)[0];
  if (longest)
    out.push({
      kind: "set_checkpoint",
      task: longest.id,
      checkpoint_interval: Math.max(1, Math.ceil(longest.duration / 4)),
    });
  return out.slice(0, 4);
}

const signed = (v: number) => (v > 0 ? `+${v}` : v < 0 ? `−${Math.abs(v)}` : "±0");

/** Главная разница — makespan: «−1 ч»; хорошо ли это — меньше лучше. */
export function headline(
  delta: Delta | null,
  unit: string,
): { text: string; tone: "good" | "bad" | "same" } | null {
  const d = delta?.makespan;
  if (d === undefined) return null;
  return { text: `${signed(d)} ${unit}`, tone: d < 0 ? "good" : d > 0 ? "bad" : "same" };
}

const NOTES: [keyof Delta, string, (u: string) => string][] = [
  ["lost_work_gpu", "потери", (u) => ` GPU·${u}`],
  ["deadline_misses", "пропуски дедлайнов", () => ""],
  ["mean_wait", "ожидание", (u) => ` ${u}`],
  ["utilization_gpu", "загрузка", () => ""],
];

/** Остальные изменившиеся метрики короткой строкой; загрузка — в процентных пунктах. */
export function deltaNote(delta: Delta | null, unit: string): string {
  if (!delta) return "вариант не дошёл до расписания";
  const parts = NOTES.flatMap(([key, label, suffix]) => {
    const v = delta[key];
    if (v === undefined || v === 0) return [];
    if (key === "utilization_gpu") return [`${label} ${signed(Math.round(v * 1000) / 10)} п.п.`];
    return [`${label} ${signed(Math.round(v * 100) / 100)}${suffix(unit)}`];
  });
  return parts.length ? parts.join(", ") : "остальные метрики не изменились";
}
