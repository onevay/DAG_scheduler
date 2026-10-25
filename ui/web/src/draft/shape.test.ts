import { describe, expect, it } from "vitest";

import { draftIssue } from "./shape";

const ok = {
  tasks: [{ id: "a", duration: 2, resources: { gpu: 1 }, dependencies: [], deadline: null }],
  cluster: { workers: [{ id: "n", resources: { gpu: 4 }, labels: { zone: "eu" } }] },
  events: [
    { time: 1, type: "worker_down", worker: "n" },
    { time: 3, type: "submit_task", task: { id: "u", duration: 1 } },
  ],
};

describe("draftIssue", () => {
  it("accepts a well-formed problem", () => {
    expect(draftIssue(ok)).toBeNull();
    expect(draftIssue({ tasks: [], cluster: { workers: [] } })).toBeNull();
  });

  it.each([
    [[], "это не объект JSON"],
    [{ ...ok, tasks: [{ duration: 2 }] }, "задача 1: нет id"],
    [{ ...ok, tasks: [{ id: "a", duration: "2" }] }, "задача 1 (a): duration — не число"],
    [
      { ...ok, tasks: [{ id: "a", duration: 2, dependencies: "b" }] },
      "задача 1 (a): неверное поле dependencies",
    ],
    [
      { ...ok, tasks: [{ id: "a", duration: 2, resources: { gpu: "4" } }] },
      "задача 1 (a): неверное поле resources",
    ],
    [{ ...ok, cluster: { workers: [{ id: "n" }] } }, "узел 1 (n): resources — не набор чисел"],
    [{ ...ok, events: [{ time: 1, type: "explode" }] }, "событие 1: неизвестный тип explode"],
    [{ ...ok, events: [{ time: 1, type: "worker_up" }] }, "событие 1: нет worker"],
    [
      { ...ok, events: [{ time: 1, type: "submit_task", task: { id: "u" } }] },
      "событие 1: задача события: duration — не число",
    ],
  ])("names what is wrong: %#", (data, issue) => {
    expect(draftIssue(data)).toBe(issue);
  });
});
