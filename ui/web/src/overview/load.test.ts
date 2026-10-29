import { describe, expect, it } from "vitest";

import type { Problem, Result } from "../api/types";
import { gapLabel, hourMatrix, nodeLoad } from "./load";

const PROBLEM: Problem = {
  tasks: [
    { id: "big", duration: 4, resources: { gpu: 4 } },
    { id: "small", duration: 2, resources: { gpu: 1 } },
  ],
  cluster: {
    workers: [
      { id: "a", resources: { gpu: 4 } },
      { id: "b", resources: { gpu: 2 } },
    ],
  },
  events: [{ time: 3, type: "submit_task", task: { id: "late", duration: 1, resources: { gpu: 2 } } }],
};

const attempt = (task: string, worker: string, start: number, end: number, extra = {}) => ({
  task,
  attempt: 1,
  worker,
  start,
  end,
  resume_from: 0,
  saved: 0,
  outcome: "DONE",
  ...extra,
});

const RESULT = {
  attempts: [
    attempt("big", "a", 0, 3, { outcome: "INT", saved: 1 }),
    attempt("big", "b", 3, 6, { attempt: 2 }),
    attempt("small", "b", 0, 2),
    attempt("late", "b", 6, 7),
  ],
  tasks: {},
  explain: {
    idle: {
      by_code: {},
      intervals: [{ worker: "a", from: 3, to: 6, code: "IDLE_DOWN", free: { gpu: 4 }, detail: {} }],
    },
  },
} as unknown as Result;

describe("overview load", () => {
  it("marks each hour busy, lost, down or idle", () => {
    expect(hourMatrix(RESULT, PROBLEM, 8)).toEqual([
      { worker: "a", hours: ["busy", "lost", "lost", "down", "down", "down", "idle", "idle"] },
      { worker: "b", hours: ["busy", "busy", "idle", "busy", "busy", "busy", "busy", "idle"] },
    ]);
  });

  it("measures GPU-hours used against capacity and time down", () => {
    const load = nodeLoad(RESULT, PROBLEM, 8);
    expect(load).toEqual([
      { worker: "a", busy: 12 / 32, down: 3 / 8 },
      // big на b просит 4 GPU из 2 — доля обрезается до 1
      { worker: "b", busy: 1, down: 0 },
    ]);
  });

  it("labels the gap to the lower bound", () => {
    expect(gapLabel(22, 21)).toBe("+4.8%");
    expect(gapLabel(21, 21)).toBe("= граница");
    expect(gapLabel(10, null)).toBeNull();
  });
});
