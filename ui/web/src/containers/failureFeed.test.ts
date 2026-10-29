import { describe, expect, it } from "vitest";

import type { JournalRecord } from "../api/types";
import { failureLine, failures, summary } from "./failureFeed";

const start = (t: number, task: string, attempt: number, worker: string, resume_from = 0) =>
  ({ type: "START", t, task, attempt, worker, resume_from }) as unknown as JournalRecord;
const cut = (t: number, task: string, attempt: number, cause: string, detail?: string, saved = 0, lost = 0) =>
  ({
    type: "INTERRUPTED",
    t,
    task,
    attempt,
    worker: "fast",
    cause,
    saved,
    lost,
    remaining: 0,
    reason: detail ? { detail } : null,
  }) as unknown as JournalRecord;

const EVENTS = [
  start(0, "train", 1, "fast"),
  start(0, "eval", 1, "fast"),
  cut(4, "train", 1, "FAILED", "chaos: контейнер убит (exit code 137)", 4, 0),
  start(4, "train", 2, "fast", 4),
  { type: "WORKER_DOWN", t: 6, worker: "fast", reason: "chaos: узел упал до 12" },
  cut(6, "train", 2, "WORKER_DOWN", undefined, 0, 2),
  cut(6, "eval", 1, "PREEMPT"),
  start(7, "train", 3, "slow", 4),
  cut(9, "train", 3, "FAILED", "kill: вручную (exit code 137)", 0, 2),
  start(9, "train", 4, "slow", 4),
  { type: "FINISH", t: 20, task: "train", attempt: 4, worker: "slow" },
  cut(21, "report", 1, "OOM"),
] as JournalRecord[];

describe("failures", () => {
  it("names each failure and the retry that followed, leaving preemptions out", () => {
    expect(failures(EVENTS).map((f) => [f.task, f.attempt, f.kind, f.retry?.from ?? null])).toEqual([
      ["train", 1, "chaos_kill", 4],
      ["train", 2, "chaos_node", 4],
      ["train", 3, "manual_kill", 4],
      ["report", 1, "oom", null],
    ]);
  });

  it("sums up by kind, recovery and work", () => {
    expect(summary(EVENTS, false)).toEqual({
      total: 4,
      byKind: { chaos_kill: 1, chaos_node: 1, manual_kill: 1, oom: 1 },
      recovered: 1,
      failed: 0,
      saved: 4,
      lost: 4,
    });
    expect(summary(EVENTS, true).failed).toBe(1); // report так и не закончилась
  });

  it("writes a line of the feed", () => {
    const [first, , , last] = failures(EVENTS);
    expect(first && failureLine(first, "мин", false)).toBe(
      "4 мин · train #1 на fast — хаос: контейнер убит; сохранено 4, потеряно 0 → повтор #2 в 4 мин на fast с позиции 4",
    );
    expect(last && failureLine(last, "мин", false)).toMatch(/→ ждёт повтора$/);
    expect(last && failureLine(last, "мин", true)).toMatch(/→ повторов больше нет$/);
  });
});
