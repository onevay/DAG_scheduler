import type { JournalRecord } from "../api/types";

/** Вид сбоя — как его называть в ленте и сводке. */
export type Kind = "chaos_kill" | "manual_kill" | "chaos_node" | "node" | "oom" | "crash";

export const KINDS: Record<Kind, string> = {
  chaos_kill: "хаос: контейнер убит",
  manual_kill: "убит вручную",
  chaos_node: "хаос: узел упал",
  node: "отказ узла",
  oom: "не хватило памяти",
  crash: "сбой контейнера",
};

export interface Failure {
  t: number;
  task: string;
  attempt: number;
  worker: string;
  kind: Kind;
  saved: number;
  lost: number;
  /** Следующая попытка задачи: когда, где и с какой позиции; null — её ещё нет (или уже не будет). */
  retry: { t: number; attempt: number; worker: string; from: number } | null;
}

type Rec<T extends JournalRecord["type"]> = Extract<JournalRecord, { type: T }>;

function kindOf(r: Rec<"INTERRUPTED">, chaosDown: ReadonlySet<string>): Kind {
  const detail = r.reason && typeof r.reason.detail === "string" ? r.reason.detail : "";
  if (r.cause === "WORKER_DOWN") return chaosDown.has(`${r.worker}@${r.t}`) ? "chaos_node" : "node";
  if (r.cause === "OOM") return "oom";
  if (detail.startsWith("chaos:")) return "chaos_kill";
  if (detail.startsWith("kill:")) return "manual_kill";
  return "crash";
}

/** Сбои попыток по журналу (вытеснения — решения политики, не сбои) и повтор, который за каждым последовал. */
export function failures(events: readonly JournalRecord[]): Failure[] {
  const chaosDown = new Set(
    events.flatMap((r) =>
      r.type === "WORKER_DOWN" && r.reason?.startsWith("chaos:") ? [`${r.worker}@${r.t}`] : [],
    ),
  );
  const starts = events.filter((r): r is Rec<"START"> => r.type === "START");
  return events
    .filter((r): r is Rec<"INTERRUPTED"> => r.type === "INTERRUPTED" && r.cause !== "PREEMPT")
    .map((r) => {
      const next = starts.find((s) => s.task === r.task && s.attempt === r.attempt + 1);
      return {
        t: r.t,
        task: r.task,
        attempt: r.attempt,
        worker: r.worker,
        kind: kindOf(r, chaosDown),
        saved: r.saved,
        lost: r.lost,
        retry: next
          ? { t: next.t, attempt: next.attempt, worker: next.worker, from: next.resume_from }
          : null,
      };
    });
}

export interface Summary {
  total: number;
  byKind: Partial<Record<Kind, number>>;
  /** Задачи, у которых были сбои и которые всё равно закончились. */
  recovered: number;
  /** Задачи, у которых были сбои и которые так и не закончились (прогон завершён). */
  failed: number;
  saved: number;
  lost: number;
}

export function summary(events: readonly JournalRecord[], done: boolean, list = failures(events)): Summary {
  const finished = new Set(events.flatMap((r) => (r.type === "FINISH" ? [r.task] : [])));
  const hit = new Set(list.map((f) => f.task));
  const byKind: Partial<Record<Kind, number>> = {};
  for (const f of list) byKind[f.kind] = (byKind[f.kind] ?? 0) + 1;
  return {
    total: list.length,
    byKind,
    recovered: [...hit].filter((t) => finished.has(t)).length,
    failed: done ? [...hit].filter((t) => !finished.has(t)).length : 0,
    saved: list.reduce((s, f) => s + f.saved, 0),
    lost: list.reduce((s, f) => s + f.lost, 0),
  };
}

/** Строка ленты: «12 мин · train_a #2 на fast — хаос: контейнер убит; сохранено 8, потеряно 3 → повтор #3 …». */
export function failureLine(f: Failure, unit: string, done: boolean): string {
  const head = `${f.t} ${unit} · ${f.task} #${f.attempt} на ${f.worker} — ${KINDS[f.kind]}; сохранено ${f.saved}, потеряно ${f.lost}`;
  const tail = f.retry
    ? `повтор #${f.retry.attempt} в ${f.retry.t} ${unit} на ${f.retry.worker} с позиции ${f.retry.from}`
    : done
      ? "повторов больше нет"
      : "ждёт повтора";
  return `${head} → ${tail}`;
}
