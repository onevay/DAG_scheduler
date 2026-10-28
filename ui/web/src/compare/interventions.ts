import type { Intervention, Problem, WhatIf } from "../api/types";
import { freeId } from "../draft/draft";
import { formatResources, resourceLabel, sortResources, workAmount, workUnit } from "../ui/resources";

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
      return `Ещё узел ${i.worker.id}: ${formatResources(i.worker.resources)}`;
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
    const id = freeId(
      "node",
      workers.map((w) => w.id),
    );
    out.push({ kind: "add_worker", worker: { id, resources: { ...largest(workers).resources } } });
  }
  const longest = [...problem.tasks]
    .filter((t) => !t.checkpoint_interval && (t.duration ?? 0) > 1)
    .sort((a, b) => (b.duration ?? 0) - (a.duration ?? 0))[0];
  if (longest)
    out.push({
      kind: "set_checkpoint",
      task: longest.id,
      checkpoint_interval: Math.max(1, Math.ceil((longest.duration ?? 4) / 4)),
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

/** Самый крупный узел: по GPU, затем по ядрам, затем по памяти. */
export function largest<W extends { resources: Readonly<Record<string, number>> }>(workers: readonly W[]): W {
  const key = (w: W) => [w.resources.gpu ?? 0, w.resources.cpu ?? 0, w.resources.ram_mb ?? 0];
  return [...workers].sort((a, b) => {
    const [x, y] = [key(a), key(b)];
    return (y[0] ?? 0) - (x[0] ?? 0) || (y[1] ?? 0) - (x[1] ?? 0) || (y[2] ?? 0) - (x[2] ?? 0);
  })[0] as W;
}

/** Остальные изменившиеся метрики короткой строкой: потери по ресурсам, пропуски, ожидание, загрузка в п.п. */
export function deltaNote(delta: Delta | null, unit: string): string {
  if (!delta) return "вариант не дошёл до расписания";
  const perResource = (prefix: string) =>
    sortResources(Object.keys(delta).flatMap((k) => (k.startsWith(prefix) ? [k.slice(prefix.length)] : [])));
  const changed = (key: string) => (delta[key] ?? 0) !== 0;
  const parts = [
    ...perResource("lost_work_").flatMap((r) => {
      const v = delta[`lost_work_${r}`] ?? 0;
      return changed(`lost_work_${r}`) ? [`потери ${signed(workAmount(r, v))} ${workUnit(r, unit)}`] : [];
    }),
    ...(changed("deadline_misses") ? [`пропуски дедлайнов ${signed(delta.deadline_misses ?? 0)}`] : []),
    ...(changed("mean_wait")
      ? [`ожидание ${signed(Math.round((delta.mean_wait ?? 0) * 100) / 100)} ${unit}`]
      : []),
    ...perResource("utilization_").flatMap((r) => {
      const v = delta[`utilization_${r}`] ?? 0;
      const label = perResource("utilization_").length > 1 ? `загрузка ${resourceLabel(r)}` : "загрузка";
      return changed(`utilization_${r}`) ? [`${label} ${signed(Math.round(v * 1000) / 10)} п.п.`] : [];
    }),
  ];
  return parts.length ? parts.join(", ") : "остальные метрики не изменились";
}
