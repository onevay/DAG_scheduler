import { describe, expect, it } from "vitest";

import { capsules, insight, profile, type Row } from "./compare";

const row = (
  policy: string,
  makespan: number | null,
  util: number | null,
  wait: number,
  lost: number,
): Row => ({
  policy,
  status: makespan === null ? "INFEASIBLE" : "OK",
  makespan,
  gapToLb: null,
  deadlineMisses: 0,
  weightedTardiness: 0,
  utilizationGpu: util,
  lostWorkGpu: lost,
  meanWait: wait,
  violations: 0,
});

const ROWS = [
  row("fifo", 11, 0.6, 1.25, 0),
  row("fifo_skip", 11, 0.6, 1.25, 0),
  row("priority_backfill", 15, 0.45, 2.5, 2),
];

describe("capsules", () => {
  it("scale by the tallest and mark every best value", () => {
    expect(capsules(ROWS, "makespan").map((c) => [c.policy, Number(c.height.toFixed(2)), c.best])).toEqual([
      ["fifo", 0.73, true],
      ["fifo_skip", 0.73, true],
      ["priority_backfill", 1, false],
    ]);
    expect(capsules(ROWS, "util").map((c) => c.best)).toEqual([true, true, false]);
  });

  it("leave a run without metrics empty", () => {
    const [c] = capsules([row("cpsat", null, null, 0, 0), ...ROWS], "makespan");
    expect(c).toEqual({ policy: "cpsat", value: null, height: 0, best: false });
  });
});

describe("profile", () => {
  it("is a share of the best value, one for the best", () => {
    const p = profile(ROWS, "priority_backfill");
    expect(Object.fromEntries(Object.entries(p).map(([k, v]) => [k, Number(v.toFixed(2))]))).toEqual({
      makespan: 0.73,
      util: 0.75,
      wait: 0.5,
      lost: 0,
    });
    expect(profile(ROWS, "fifo")).toEqual({ makespan: 1, util: 1, wait: 1, lost: 1 });
  });
});

describe("insight", () => {
  it("names the best and the worst or says there is no difference", () => {
    expect(insight(ROWS, "makespan", "ч")).toBe(
      "Лучше всех — fifo, fifo_skip (11 ч), хуже всех — priority_backfill (15 ч).",
    );
    expect(insight(ROWS.slice(0, 2), "wait", "ч")).toBe(
      "По метрике «Ожидание» политики не различаются: у всех 1.25 ч.",
    );
    expect(insight([row("x", null, null, 0, 0)], "makespan", "ч")).toBe(
      "Ни один прогон не дошёл до расписания — метрик нет.",
    );
  });
});
