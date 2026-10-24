import { describe, expect, it } from "vitest";

import type { InputEvent } from "../api/types";
import { defaultOutage, horizonOf, outages, ticks } from "./timeline";

const down = (worker: string, time: number): InputEvent => ({ time, type: "worker_down", worker });
const up = (worker: string, time: number): InputEvent => ({ time, type: "worker_up", worker });

describe("outages", () => {
  it("pairs downs with ups and keeps an unreturned worker down until the end", () => {
    const events = [up("a", 8), down("a", 3), down("b", 10), { time: 4, type: "submit_task" } as InputEvent];
    expect(outages(events, 20)).toEqual([
      { worker: "a", from: 3, to: 8 },
      { worker: "b", from: 10, to: 20 },
    ]);
  });

  it("ignores a second down and an up without a down", () => {
    expect(outages([down("a", 2), down("a", 5), up("a", 9), up("a", 12)], 20)).toEqual([
      { worker: "a", from: 2, to: 9 },
    ]);
  });
});

describe("scale", () => {
  it("covers the lower bound and the last event with a margin", () => {
    expect(horizonOf([], undefined)).toBe(4);
    expect(horizonOf([down("a", 30)], 21)).toBe(36);
    expect(horizonOf([], 21)).toBe(26);
  });

  it("picks a round tick step", () => {
    expect(ticks(22)).toEqual([0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22]);
    expect(ticks(100)).toEqual([0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100]);
    expect(ticks(7)).toEqual([0, 1, 2, 3, 4, 5, 6, 7]);
  });

  it("suggests an outage in the second quarter", () => {
    expect(defaultOutage(24)).toEqual({ from: 6, to: 12 });
    expect(defaultOutage(4)).toEqual({ from: 1, to: 2 });
  });
});
