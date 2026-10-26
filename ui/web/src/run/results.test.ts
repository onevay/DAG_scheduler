import { describe, expect, it } from "vitest";

import type { JournalRecord, Problem, Result } from "../api/types";
import {
  assignLanes,
  comparisonRows,
  ganttBars,
  journalOutages,
  laneOrder,
  replanRows,
  reservations,
  taskRows,
} from "./results";

type Attempt = Result["attempts"][number];
const attempt = (
  task: string,
  worker: string,
  start: number,
  end: number | null,
  extra: Partial<Attempt> = {},
) => ({
  task,
  attempt: 1,
  worker,
  start,
  end,
  resume_from: 0,
  saved: 0,
  outcome: "DONE" as const,
  ...extra,
});

const taskOut = (extra: Partial<Result["tasks"][string]> = {}): Result["tasks"][string] => ({
  status: "SUCCEEDED",
  start: 0,
  finish: 1,
  deadline: null,
  tardiness: null,
  waits: [],
  ...extra,
});

function result(attempts: Attempt[], tasks: Result["tasks"] = {}): Result {
  return { attempts, tasks, metrics: null, violations: [], replans: null } as unknown as Result;
}

const PROBLEM: Problem = {
  tasks: [
    { id: "pretrain", duration: 6, kind: "train" },
    { id: "eval", duration: 2, kind: "eval" },
    { id: "prep", duration: 2 },
  ],
  cluster: { workers: [{ id: "node-A", resources: { gpu: 4 } }] },
  events: [{ time: 9, type: "submit_task", task: { id: "urgent", duration: 3, kind: "eval" } }],
};

describe("ganttBars", () => {
  const bars = ganttBars(
    result(
      [
        attempt("pretrain", "node-A", 2, 5, { outcome: "INT", saved: 2 }),
        attempt("pretrain", "node-B", 5, 11, { attempt: 2 }),
        attempt("eval", "node-A", 2, 4),
        attempt("urgent", "node-B", 11, 14),
        attempt("prep", "node-A", 0, null, { outcome: null }),
      ],
      { eval: taskOut({ tardiness: 1 }), urgent: taskOut() },
    ),
    PROBLEM,
  );

  it("keeps concurrent attempts on different lanes and reuses freed ones", () => {
    const byLane = new Map<string, [number, number][]>();
    for (const b of bars) byLane.set(b.lane, [...(byLane.get(b.lane) ?? []), [b.start, b.end]]);
    for (const spans of byLane.values()) {
      spans.sort((a, b) => a[0] - b[0]);
      spans.slice(1).forEach(([start], i) => expect(spans[i]?.[1]).toBeLessThanOrEqual(start));
    }
    expect(bars.map((b) => [b.task, b.lane])).toEqual([
      ["prep", "node-A · 1"],
      // при равном старте — по id задачи: eval раньше pretrain
      ["eval", "node-A · 1"],
      ["pretrain", "node-A · 2"],
      ["pretrain", "node-B · 1"],
      ["urgent", "node-B · 1"],
    ]);
  });

  it("shows where interrupted work was lost and marks kind, submission and lateness", () => {
    const [first, second] = bars.filter((b) => b.task === "pretrain");
    expect([first?.lostFrom, first?.end, second?.lostFrom]).toEqual([4, 5, null]);
    expect(bars.find((b) => b.task === "urgent")).toMatchObject({
      submitted: true,
      kind: "eval",
      late: false,
    });
    expect(bars.find((b) => b.task === "eval")).toMatchObject({ late: true, kind: "eval" });
    expect(bars.find((b) => b.task === "prep")).toMatchObject({ kind: "other", end: 0 });
  });

  it("treats a preempted attempt like an interrupted one", () => {
    const [bar] = ganttBars(result([attempt("eval", "n", 0, 3, { outcome: "PRE", saved: 2 })]), PROBLEM);
    expect([bar?.outcome, bar?.lostFrom, bar?.end]).toEqual(["PRE", 2, 3]);
  });
});

describe("lanes", () => {
  it("are numbered from one and grow as needed", () => {
    const bars = Array.from({ length: 11 }, (_, i) => ({ task: `t${i}`, worker: "n", start: 0, end: 1 }));
    const lanes = assignLanes(bars).map((b) => b.lane);
    expect(lanes.slice(0, 3)).toEqual(["n · 1", "n · 2", "n · 3"]);
    expect(lanes.at(-1)).toBe("n · 11");
  });

  it("give every worker a lane even when idle", () => {
    const bars = [
      { worker: "b", laneIndex: 1 },
      { worker: "b", laneIndex: 0 },
    ];
    expect(laneOrder(bars, ["c", "a", "b"])).toEqual(["a · 1", "b · 1", "b · 2", "c · 1"]);
  });
});

describe("journal", () => {
  const rec = (r: object) => r as JournalRecord;

  it("finds outages; a worker that never returns is down until the end", () => {
    const events = [
      rec({ type: "WORKER_DOWN", t: 5, worker: "node-A", seq: 0 }),
      rec({ type: "START", t: 6, task: "x", worker: "node-B", seq: 1 }),
      rec({ type: "WORKER_UP", t: 15, worker: "node-A", seq: 2 }),
      rec({ type: "WORKER_DOWN", t: 7, worker: "n", seq: 3 }),
    ];
    expect(journalOutages(events, 20)).toEqual([
      { worker: "node-A", from: 5, to: 15 },
      { worker: "n", from: 7, to: 20 },
    ]);
  });

  it("keeps the last reservation of each task", () => {
    const events = [
      rec({ type: "RESERVE", t: 0, task: "big", worker: "n0", t_res: 4 }),
      rec({ type: "START", t: 1, task: "short" }),
      rec({ type: "RESERVE", t: 2, task: "big", worker: "n1", t_res: 3 }),
    ];
    expect(reservations(events)).toEqual([{ task: "big", worker: "n1", tRes: 3, since: 2 }]);
  });

  it("shows replans one per row", () => {
    const replans = [
      {
        seq: 0,
        type: "REPLAN",
        trigger: "event",
        t: 5,
        events: [
          { type: "worker_down", worker: "node-A" },
          { type: "submit_task", task: "u" },
        ],
        changes: [{ task: "ft_det", old_start: 10, new_start: 11, delta: 1 }],
        new_tasks: { pretrain_vit: 5 },
        dropped: ["b"],
      },
    ] as unknown as Result["replans"];
    expect(replanRows(replans)).toEqual([
      {
        t: 5,
        events: "worker_down node-A, submit_task u",
        shifted: "ft_det +1",
        added: "pretrain_vit → 5",
        dropped: "b",
      },
    ]);
  });
});

describe("tables", () => {
  it("count attempts, sum waits and name the main reason", () => {
    const r = result([attempt("b", "n", 5, 6), attempt("b", "n", 6, 7, { attempt: 2 })], {
      b: taskOut({
        start: 5,
        finish: 7,
        waits: [
          { code: "WAIT_DEPS", from: 0, to: 1, detail: {} },
          { code: "WAIT_RESOURCES", from: 1, to: 5, detail: {} },
        ],
      }),
    });
    expect(taskRows(r)).toEqual([
      {
        task: "b",
        status: "SUCCEEDED",
        attempts: 2,
        start: 5,
        finish: 7,
        deadline: null,
        tardiness: null,
        waited: 5,
        mainReason: "WAIT_RESOURCES",
      },
    ]);
  });

  it("compare runs one row per policy", () => {
    const metrics = {
      makespan: 21,
      lower_bound: 21,
      gap_to_lb: 0,
      deadline_misses: { critical: 1, high: 0, normal: 2, low: 0 },
      weighted_tardiness: 3,
      utilization: { gpu: 0.5 },
      lost_work: { gpu: 0 },
      mean_wait: 0.1,
      score: { objective: "weighted", key: [24] },
    };
    const run = (status: string, m: typeof metrics | null) =>
      ({ status, metrics: m, violations: m ? [] : ["x"] }) as unknown as Result;
    const rows = comparisonRows({ fifo: run("OK", metrics), priority: run("INFEASIBLE", null) });
    expect(rows.map((r) => [r.policy, r.makespan, r.deadlineMisses, r.score, r.violations])).toEqual([
      ["fifo", 21, 3, [24], 0],
      ["priority", null, null, null, 1],
    ]);
  });
});
