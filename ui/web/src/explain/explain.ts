import type { Result } from "../api/types";

type Explain = NonNullable<Result["explain"]>;
export type Link = Explain["chain"][number];
export type Idle = Explain["idle"]["intervals"][number];
type Part = keyof Explain["decomposition"];

const CAUSES: Record<string, string> = {
  origin: "старт",
  dep: "зависимость",
  fail: "отказ",
  pre: "вытеснение",
  res: "место",
  rel: "появилась",
  up: "возврат узла",
  wait: "ожидание",
};

export const causeLabel = (cause: string) => CAUSES[cause] ?? cause;

/** Почему звено цепочки стартовало именно тогда — одной-двумя фразами. */
export function stepText(l: Link, unit: string): string {
  const why: Record<string, string> = {
    origin: `${l.task} стартует в ${l.start}: её ничто не держит, а на ${l.worker} есть место.`,
    dep: `Старт в ${l.start} — ровно когда закончилась ${l.by}.`,
    fail: `Попытка ${l.attempt} стартует в ${l.start} на ${l.worker}: прошлую попытку прервал отказ узла.`,
    pre: `Попытка ${l.attempt} стартует в ${l.start} на ${l.worker}: прошлую попытку вытеснила задача важнее.`,
    res: `Старт в ${l.start}, когда на ${l.worker} освободилось место после ${l.by}.`,
    rel: `Старт в ${l.start} — в момент, когда задача появилась (release или подача во время прогона).`,
    up: `Старт в ${l.start} — когда ${l.worker} вернулся в строй.`,
    wait: `Старт в ${l.start}: держала не зависимость и не нехватка места, а решение политики.`,
  };
  const done =
    l.lost > 0 ? ` Сделано ${l.work} ${unit}, ${l.lost} ${unit} потеряно.` : ` Работа — ${l.work} ${unit}.`;
  return (why[l.cause] ?? `Старт в ${l.start}.`) + done;
}

export const PARTS: readonly { key: Part; label: string; formula: string; color: string }[] = [
  {
    key: "dep",
    label: "работа по цепочке",
    formula: "работа по цепочке",
    color: "var(--color-blueprint-text)",
  },
  { key: "res", label: "ожидание места", formula: "ожидание места", color: "var(--color-blueprint-focus)" },
  { key: "lost", label: "потери", formula: "потеря при отказе", color: "var(--color-accent)" },
  { key: "release", label: "появление задач", formula: "до появления задач", color: "var(--kind-infer)" },
  { key: "wait", label: "ожидание", formula: "ожидание политики", color: "var(--color-warning)" },
];

/** «= 21 работа по цепочке + 1 потеря при отказе»: только ненулевые слагаемые. */
export function formula(d: Explain["decomposition"]): string[] {
  const parts = PARTS.filter((p) => d[p.key] > 0).map((p) => `${d[p.key]} ${p.formula}`);
  return parts.map((p, i) => `${i === 0 ? "=" : "+"} ${p}`);
}

export const CODES: Record<string, { label: string; hint: string; color: string }> = {
  IDLE_NO_READY: {
    label: "нет готовых",
    hint: "нет готовых задач, подходящих узлу",
    color: "var(--idle-no-ready)",
  },
  IDLE_DOWN: { label: "узел лежит", hint: "узел недоступен", color: "var(--hatch-down)" },
  IDLE_FRAGMENTATION: {
    label: "не помещается",
    hint: "готовая задача не помещается в свободное место",
    color: "var(--color-blueprint-focus)",
  },
  IDLE_RESERVED: { label: "резерв", hint: "место держит резерв backfill", color: "var(--kind-train)" },
  IDLE_POLICY: {
    label: "политика",
    hint: "политика не стала запускать готовую задачу",
    color: "var(--color-warning)",
  },
};

export interface Cell {
  /** Причина простоя в этот час; null — узел занят целиком. */
  code: string | null;
  /** Простаивает часть GPU узла, а не все. */
  part: boolean;
}

/** Простой по узлам и часам: в каждой клетке — причина, державшая больше всего GPU в этот час. */
export function idleGrid(
  intervals: readonly Idle[],
  workers: readonly { id: string; gpu: number }[],
  horizon: number,
): { worker: string; cells: Cell[] }[] {
  return workers.map(({ id, gpu }) => {
    const mine = intervals.filter((i) => i.worker === id);
    const cells = Array.from({ length: horizon }, (_, h) => {
      const free = (i: Idle) => i.free.gpu ?? 0;
      const here = mine
        .filter((i) => i.from <= h && h < i.to && free(i) > 0)
        .sort((a, b) => free(b) - free(a))[0];
      return here ? { code: here.code, part: free(here) < gpu } : { code: null, part: false };
    });
    return { worker: id, cells };
  });
}

/** Простой по кодам по убыванию, без нулевых; и одна фраза о главной причине. */
export function codeSummary(byCode: Readonly<Record<string, number>>, unit: string) {
  const rows = Object.entries(byCode)
    .filter(([, v]) => v > 0)
    .sort(([, a], [, b]) => b - a);
  const total = rows.reduce((s, [, v]) => s + v, 0);
  const top = rows[0];
  const insight = top
    ? `Больше всего простоя — ${top[0]}: ${top[1]} из ${total} GPU·${unit} (${CODES[top[0]]?.hint ?? top[0]}).`
    : "GPU не простаивали.";
  return { rows, total, insight };
}
