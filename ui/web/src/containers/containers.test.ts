import { describe, expect, it } from "vitest";

import type { Container, JournalRecord } from "../api/types";
import { attemptLog, expectation, p80Work } from "./containers";

const container = { task: "train", attempt: 2, worker: "w", progress: 6, state: "started" } as Container;
const events = [
  { type: "START", t: 10, task: "train", attempt: 2, worker: "w", resume_from: 6 },
  { type: "ESTIMATE", t: 14, task: "train", attempt: 2, work: 12, spread: 0.4, end: 22 },
  { type: "ESTIMATE", t: 14, task: "train", attempt: 1, work: 99, spread: 0.4, end: 99 },
] as unknown as JournalRecord[];

describe("expectation", () => {
  it("uses the known duration as is", () => {
    expect(expectation(events, container, 20, undefined)).toEqual({ expected: 20, p80: 20, approx: false });
  });

  it("takes the last refinement of this attempt from its start position", () => {
    expect(expectation(events, container, null, { mean: 30, p80: 40 })).toEqual({
      expected: 18,
      p80: 6 + p80Work(12, 0.4),
      approx: true,
    });
  });

  it("falls back to the server estimate before any refinement", () => {
    expect(expectation([], container, undefined, { mean: 30, p80: 40 })).toEqual({
      expected: 30,
      p80: 40,
      approx: true,
    });
    expect(expectation([], container, undefined, undefined)).toBeUndefined();
  });

  it("matches the server's 80th percentile", () => {
    expect([p80Work(20, 0), p80Work(20, 0.5)]).toEqual([20, 27]);
  });

  it("puts refinements into the attempt log", () => {
    expect(attemptLog(events, container, "ч")).toEqual([
      "10 ч · старт на w с позиции 6",
      "14 ч · уточнение: работа попытки ≈ 12 ч, окончание к 22 ч",
    ]);
  });
});
