/** Ресурсы задач и узлов: основные — ядра, ОЗУ в МиБ и GPU; остальные имена показываются как есть. */

export type Resources = Readonly<Record<string, number>>;

const ORDER = ["cpu", "ram_mb", "gpu"];
const LABELS: Record<string, string> = { cpu: "CPU", ram_mb: "ОЗУ", gpu: "GPU" };

/** Ресурсы в порядке «ядра, память, GPU, остальные по алфавиту». */
export function sortResources(names: Iterable<string>): string[] {
  const rank = (r: string) => (ORDER.includes(r) ? ORDER.indexOf(r) : ORDER.length);
  return [...new Set(names)].sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
}

export const resourceLabel = (r: string) => LABELS[r] ?? r;

const round1 = (v: number) => String(Math.round(v * 10) / 10);

/** Количество ресурса для подписи: «2 CPU», «4 ГБ», «512 МБ», «1 GPU». */
export function amount(r: string, n: number): string {
  if (r === "ram_mb") return n >= 1024 || n === 0 ? `${round1(n / 1024)} ГБ` : `${n} МБ`;
  return `${n} ${resourceLabel(r)}`;
}

/** Ненулевые ресурсы одной строкой: «2 CPU · 4 ГБ»; без ресурсов — «без ресурсов». */
export function formatResources(res: Resources | undefined): string {
  const parts = sortResources(Object.keys(res ?? {}))
    .filter((r) => (res?.[r] ?? 0) > 0)
    .map((r) => amount(r, res?.[r] ?? 0));
  return parts.length ? parts.join(" · ") : "без ресурсов";
}

/** Ресурс × время для потерь и простоя: число (память — в ГБ), единица и всё вместе. */
export const workAmount = (r: string, n: number) => (r === "ram_mb" ? Math.round((n / 1024) * 10) / 10 : n);
export const workUnit = (r: string, unit: string) => `${r === "ram_mb" ? "ГБ" : resourceLabel(r)}·${unit}`;
export const work = (r: string, n: number, unit: string) => `${workAmount(r, n)} ${workUnit(r, unit)}`;

/** Основной ресурс для сводок: GPU, если он есть, иначе ядра, иначе память, иначе первый по алфавиту. */
export function mainResource(names: Iterable<string>): string | null {
  const sorted = sortResources(names);
  return sorted.includes("gpu") ? "gpu" : (sorted[0] ?? null);
}

/** Ресурсы узлов кластера. */
export function clusterResources(workers: readonly { resources: Resources }[]): string[] {
  return sortResources(
    workers.flatMap((w) => Object.keys(w.resources).filter((r) => (w.resources[r] ?? 0) > 0)),
  );
}

/** Помещается ли задача на узел: хватает каждого ресурса, который она просит. */
export const fits = (need: Resources | undefined, capacity: Resources) =>
  Object.entries(need ?? {}).every(([r, n]) => n <= (capacity[r] ?? 0));
