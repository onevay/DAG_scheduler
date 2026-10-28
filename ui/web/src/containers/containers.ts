import type { Container, JournalRecord, Problem, Usage } from "../api/types";
import { amount, type Resources } from "../ui/resources";

export type Mode = NonNullable<Problem["mode"]>;

export const MODES: Record<Mode, { label: string; text: string }> = {
  simulate: {
    label: "Симуляция",
    text: "Время модели идёт мгновенно, задачи не запускаются. Всё, что видно на других экранах, — этот режим.",
  },
  emulate: {
    label: "Эмуляция",
    text: "Каждая попытка задачи — настоящий контейнер на агенте узла. Планировщик тот же; время модели идёт в заданном масштабе, а отказ узла останавливает его контейнеры.",
  },
  shadow: {
    label: "Shadow",
    text: "Контейнеры работают как в эмуляции, а рядом считается план: видно, где факт разошёлся с планом и почему.",
  },
  replay: {
    label: "Replay",
    text: "Повтор сохранённого журнала теми же фактами, без контейнеров: те же ли решения принимает политика.",
  },
};

/** Масштаб времени: сколько миллисекунд стены длится единица времени модели. */
export const SCALES = [100, 500, 1000, 5000, 60_000] as const;
export const scaleLabel = (ms: number) =>
  ms >= 60_000 ? `${ms / 60_000} мин` : ms >= 1000 ? `${ms / 1000} с` : `${ms} мс`;

export type Phase = "running" | "waiting" | "killed" | "done";
export type Filter = "all" | Phase;

export const FILTERS: readonly { value: Filter; label: string }[] = [
  { value: "all", label: "все" },
  { value: "running", label: "идут" },
  { value: "killed", label: "убиты" },
  { value: "waiting", label: "ждут" },
  { value: "done", label: "завершены" },
];

const STATES: Record<string, [string, Phase]> = {
  sent: ["отправлен агенту", "waiting"],
  accepted: ["создаётся", "waiting"],
  started: ["идёт", "running"],
  checkpoint: ["идёт, чекпоинт сохранён", "running"],
  exited: ["завершён", "done"],
  oom: ["убит: не хватило памяти", "killed"],
  killed: ["остановлен", "killed"],
  lost: ["потерян", "killed"],
  rejected: ["отклонён агентом", "killed"],
};

export const stateLabel = (c: Container) =>
  c.state === "exited" && c.detail ? "завершён с ошибкой" : (STATES[c.state]?.[0] ?? c.state);
export const phase = (c: Container): Phase => STATES[c.state]?.[1] ?? "waiting";

export function counts(containers: readonly Container[]): Record<Filter, number> {
  const out: Record<Filter, number> = { all: containers.length, running: 0, waiting: 0, killed: 0, done: 0 };
  for (const c of containers) out[phase(c)] += 1;
  return out;
}

/** Доля работы задачи, сохранённой попыткой: законченная — целиком, иначе по последнему чекпоинту. */
export function share(c: Container, duration: number | undefined): number {
  if (c.state === "exited" && !c.detail) return 1;
  return duration ? Math.min(1, c.progress / duration) : 0;
}

/** Что было с попыткой — по записям журнала планировщика. */
export function attemptLog(events: readonly JournalRecord[], c: Container, unit: string): string[] {
  const out: string[] = [];
  for (const r of events) {
    if (!("task" in r) || r.task !== c.task || !("attempt" in r) || r.attempt !== c.attempt) continue;
    if (r.type === "START") out.push(`${r.t} ${unit} · старт на ${r.worker} с позиции ${r.resume_from}`);
    else if (r.type === "FINISH") out.push(`${r.t} ${unit} · закончилась`);
    else if (r.type === "INTERRUPTED")
      out.push(`${r.t} ${unit} · прервана (${r.cause}): сохранено ${r.saved}, потеряно ${r.lost}`);
    else if (r.type === "ESTIMATE")
      out.push(
        `${r.t} ${unit} · уточнение: работа попытки ≈ ${r.work} ${unit}, окончание к ${r.end} ${unit}`,
      );
  }
  return out;
}

/** Квантиль 0,8 логнормального по его среднему и σ логарифма — как у сервера. */
export const p80Work = (work: number, spread: number) =>
  Math.max(work, Math.ceil(work * Math.exp(0.8416 * spread - (spread * spread) / 2)));

export type Expectation = { expected: number; p80: number; approx: boolean };

type Prior = { mean: number; p80: number } | undefined;

/** Сколько всего работы ждут от задачи попытки `c`: длительность, если известна, иначе оценка с последним
 * уточнением этой попытки (оно считает работу от её стартовой позиции). */
export function expectation(
  events: readonly JournalRecord[],
  c: Container,
  duration: number | null | undefined,
  prior: Prior,
): Expectation | undefined {
  if (duration !== null && duration !== undefined)
    return { expected: duration, p80: duration, approx: false };
  let resume = 0;
  let last: { work: number; spread: number } | undefined;
  for (const r of events) {
    if (!("task" in r) || r.task !== c.task || !("attempt" in r) || r.attempt !== c.attempt) continue;
    if (r.type === "START") resume = r.resume_from;
    else if (r.type === "ESTIMATE") last = r;
  }
  if (last)
    return { expected: resume + last.work, p80: resume + p80Work(last.work, last.spread), approx: true };
  return prior && { expected: prior.mean, p80: prior.p80, approx: true };
}

/** Узлы без агента: прогон на агентах для такого входа не запустится. */
export const withoutAgent = (problem: Problem) =>
  problem.cluster.workers.filter((w) => !w.agent).map((w) => w.id);

export interface Meter {
  label: string;
  text: string;
  share: number | null; // доля лимита задачи; без лимита — null
  near: boolean; // память у самого предела: ещё немного — и OOM
}

const cores = (n: number) => n.toLocaleString("ru-RU", { maximumFractionDigits: 1 });

/** Потребление контейнера против ресурсов его задачи (они же — лимиты контейнера). */
export function usageMeters(usage: Usage, limits: Resources | undefined): Meter[] {
  const cpu = usage.cpu_milli / 1000;
  const cpuLimit = limits?.cpu;
  const ramLimit = limits?.ram_mb;
  const ramShare = ramLimit ? usage.ram_mb / ramLimit : null;
  return [
    {
      label: "CPU",
      text: cpuLimit ? `${cores(cpu)} из ${cpuLimit} ядер` : `${cores(cpu)} ядер`,
      share: cpuLimit ? cpu / cpuLimit : null,
      near: false,
    },
    {
      label: "ОЗУ",
      text: ramLimit
        ? `${amount("ram_mb", usage.ram_mb)} из ${amount("ram_mb", ramLimit)}`
        : amount("ram_mb", usage.ram_mb),
      share: ramShare,
      near: ramShare !== null && ramShare >= 0.9,
    },
  ];
}
