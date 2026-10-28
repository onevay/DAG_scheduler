import type { JournalRecord } from "../api/types";
import type { FeedItem } from "../ui/data";
import type { GanttBar } from "./results";

export type Phase = "done" | "now" | "future";

/** Где попытка относительно момента `t`: уже прошла, идёт или ещё впереди. */
export function phaseAt(bar: Pick<GanttBar, "start" | "end">, t: number): Phase {
  if (bar.end <= t) return "done";
  return bar.start <= t ? "now" : "future";
}

/** Строка журнала по-русски: что случилось, без служебных полей. */
export function describe(r: JournalRecord, unit: string): string {
  switch (r.type) {
    case "START":
      return `${r.task} → ${r.worker}${r.attempt > 1 ? `, попытка ${r.attempt}` : ""}${r.resume_from > 0 ? ` с позиции ${r.resume_from}` : ""}`;
    case "FINISH":
      return `${r.task} закончена на ${r.worker}`;
    case "INTERRUPTED":
      return `${r.task} прервана на ${r.worker}: сохранено ${r.saved} ${unit}, потеряно ${r.lost}`;
    case "WORKER_DOWN":
      return `${r.worker} недоступен`;
    case "WORKER_UP":
      return `${r.worker} вернулся пустым`;
    case "SUBMIT":
      return `${r.task}, priority ${r.priority}${r.deadline != null ? `, дедлайн ${r.deadline}` : ""}`;
    case "WAIT":
      return `${r.task} ждёт: ${r.code}`;
    case "REPLAN":
      return r.changes.length ? `сдвинулись старты ${r.changes.length} задач` : "сдвигов нет";
    case "RESERVE":
      return `${r.task}: место на ${r.worker} к ${r.t_res} ${unit}`;
    case "BLOCKED":
      return `${r.task} не выполнится: из-за ${r.by}`;
    case "DIAGNOSTIC":
      return `${r.task}: ${r.code}`;
    case "READY":
      return `${r.task} готова к старту`;
    case "ESTIMATE":
      return `${r.task}: работа попытки ≈ ${r.work} ${unit}, окончание к ${r.end} ${unit}`;
  }
}

// READY пишется на каждый шаг готовности и заслоняет то, что важно в ленте
const QUIET = new Set<JournalRecord["type"]>(["READY"]);

/** Последние записи журнала к моменту `t`, новые сверху. */
export function feedAt(events: readonly JournalRecord[], t: number, unit: string, limit = 5): FeedItem[] {
  return events
    .filter((r) => r.t <= t && !QUIET.has(r.type))
    .slice(-limit)
    .reverse()
    .map((r) => ({ key: String(r.seq), time: `${r.t} ${unit}`, type: r.type, text: describe(r, unit) }));
}

const MARKS: Partial<Record<JournalRecord["type"], string>> = {
  WORKER_DOWN: "отказ",
  WORKER_UP: "возврат",
  SUBMIT: "подача",
};

/** Моменты, к которым стоит перейти одной кнопкой: отказы, возвраты, подачи задач. */
export function marksOf(events: readonly JournalRecord[]): { t: number; label: string }[] {
  const seen = new Set<string>();
  return events.flatMap((r) => {
    const kind = MARKS[r.type];
    const label = kind && `${kind} ${r.t}`;
    if (!label || seen.has(label)) return [];
    seen.add(label);
    return [{ t: r.t, label }];
  });
}
