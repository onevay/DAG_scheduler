import { describe as group, expect, it } from "vitest";

import type { Problem } from "../api/types";
import { deltaNote, describe, headline, suggest } from "./interventions";

const PROBLEM: Problem = {
  tasks: [
    { id: "prep", duration: 2 },
    { id: "pretrain", duration: 8, checkpoint_interval: 2 },
    { id: "train", duration: 6 },
  ],
  cluster: {
    workers: [
      { id: "node-A", resources: { gpu: 4 } },
      { id: "node-B", resources: { gpu: 2 } },
    ],
  },
  events: [
    { time: 5, type: "worker_down", worker: "node-A" },
    { time: 9, type: "worker_up", worker: "node-A" },
  ],
};

group("interventions", () => {
  it("suggest removing failures, a node like the largest and a checkpoint for the longest task", () => {
    expect(suggest(PROBLEM)).toEqual([
      { kind: "remove_failures", worker: "node-A" },
      { kind: "add_worker", worker: { id: "node_1", resources: { gpu: 4 } } },
      { kind: "set_checkpoint", task: "train", checkpoint_interval: 2 },
    ]);
  });

  it("are described in one phrase", () => {
    expect(
      [
        { kind: "set_checkpoint", task: "a", checkpoint_interval: null },
        { kind: "set_preemptible", task: "a", preemptible: false },
        { kind: "set_priority", task: "a", priority: 10 },
        { kind: "set_deadline", task: "a", deadline: 14 },
        { kind: "remove_failures", worker: "n" },
      ].map((i) => describe(i as Parameters<typeof describe>[0], "ч")),
    ).toEqual([
      "a без чекпоинтов",
      "a нельзя вытеснять",
      "Приоритет a — 10",
      "Дедлайн a — 14 ч",
      "Без отказов n",
    ]);
  });

  it("turn deltas into a headline and a note", () => {
    expect(headline({ makespan: -1 }, "ч")).toEqual({ text: "−1 ч", tone: "good" });
    expect(headline({ makespan: 2 }, "ч")).toEqual({ text: "+2 ч", tone: "bad" });
    expect(headline(null, "ч")).toBeNull();
    expect(
      deltaNote({ makespan: -1, lost_work_gpu: -4, mean_wait: -0.0909, utilization_gpu: -0.069 }, "ч"),
    ).toBe("потери −4 GPU·ч, ожидание −0.09 ч, загрузка −6.9 п.п.");
    expect(deltaNote({ makespan: 0, deadline_misses: 0 }, "ч")).toBe("остальные метрики не изменились");
    expect(deltaNote(null, "ч")).toBe("вариант не дошёл до расписания");
  });
});
