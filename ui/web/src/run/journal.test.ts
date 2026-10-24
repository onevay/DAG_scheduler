import { describe, expect, it } from "vitest";

import type { JournalRecord } from "../api/types";
import { describe as text, feedAt, marksOf, phaseAt } from "./journal";

const rec = (r: object) => r as JournalRecord;
const JOURNAL = [
  rec({ seq: 0, t: 0, type: "READY", task: "a" }),
  rec({ seq: 1, t: 0, type: "START", task: "a", worker: "n", attempt: 1, resume_from: 0, reason: {} }),
  rec({ seq: 2, t: 5, type: "WORKER_DOWN", worker: "n" }),
  rec({
    seq: 3,
    t: 5,
    type: "INTERRUPTED",
    task: "a",
    worker: "n",
    attempt: 1,
    saved: 2,
    lost: 1,
    remaining: 3,
    cause: "down",
  }),
  rec({ seq: 4, t: 5, type: "START", task: "a", worker: "m", attempt: 2, resume_from: 2, reason: {} }),
  rec({ seq: 5, t: 9, type: "SUBMIT", task: "u", priority: 10, deadline: 14 }),
  rec({ seq: 6, t: 15, type: "WORKER_UP", worker: "n" }),
  rec({ seq: 7, t: 15, type: "WORKER_DOWN", worker: "n" }),
  rec({ seq: 8, t: 15, type: "WORKER_DOWN", worker: "k" }),
];

describe("journal", () => {
  it("places an attempt relative to the moment", () => {
    const bar = { start: 2, end: 5 };
    expect([phaseAt(bar, 1), phaseAt(bar, 2), phaseAt(bar, 4.5), phaseAt(bar, 5)]).toEqual([
      "future",
      "now",
      "now",
      "done",
    ]);
  });

  it("describes records in Russian", () => {
    expect(JOURNAL.slice(1, 6).map((r) => text(r, "ч"))).toEqual([
      "a → n",
      "n недоступен",
      "a прервана на n: сохранено 2 ч, потеряно 1",
      "a → m, попытка 2 с позиции 2",
      "u, priority 10, дедлайн 14",
    ]);
  });

  it("feeds the latest records up to the moment, newest first, without READY", () => {
    const feed = feedAt(JOURNAL, 5, "ч", 3);
    expect(feed.map((f) => [f.key, f.time, f.type])).toEqual([
      ["4", "5 ч", "START"],
      ["3", "5 ч", "INTERRUPTED"],
      ["2", "5 ч", "WORKER_DOWN"],
    ]);
    expect(feedAt(JOURNAL, 0, "ч").map((f) => f.type)).toEqual(["START"]);
  });

  it("offers one jump per kind and moment", () => {
    expect(marksOf(JOURNAL)).toEqual([
      { t: 5, label: "отказ 5" },
      { t: 9, label: "подача 9" },
      { t: 15, label: "возврат 15" },
      { t: 15, label: "отказ 15" },
    ]);
  });
});
