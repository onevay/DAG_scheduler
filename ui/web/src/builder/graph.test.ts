import { describe, expect, it } from "vitest";

import type { Task } from "../api/types";
import { graphEdges, graphTasks, layeredLayout, layers } from "./graph";

const task = (id: string, ...dependencies: string[]): Task => ({ id, duration: 1, dependencies });

describe("layers", () => {
  it("is the longest dependency chain", () => {
    const depth = layers([task("a"), task("b", "a"), task("c", "a"), task("d", "b", "c", "a"), task("e")]);
    expect(Object.fromEntries(depth)).toEqual({ a: 0, b: 1, c: 1, d: 2, e: 0 });
  });

  it("stays finite on cycles and ignores unknown or self dependencies", () => {
    const depth = layers([task("a", "c"), task("b", "a"), task("c", "b"), task("x", "x", "ghost")]);
    expect(Math.max(...depth.values())).toBeLessThan(4);
    expect(depth.get("x")).toBe(0);
  });
});

describe("layeredLayout", () => {
  it("puts layers in columns and keeps input order within a layer", () => {
    const xy = layeredLayout([task("a"), task("b", "a"), task("c", "a"), task("d")]);
    expect(xy.get("a")).toEqual({ x: 0, y: 0 });
    expect(xy.get("d")).toEqual({ x: 0, y: 76 });
    expect(xy.get("b")).toEqual({ x: 176, y: 0 });
    expect(xy.get("c")).toEqual({ x: 176, y: 76 });
  });
});

describe("graph", () => {
  it("adds submitted tasks and marks critical edges", () => {
    const tasks = graphTasks({
      tasks: [task("a"), task("b", "a", "a"), task("c", "b")],
      cluster: { workers: [] },
      events: [
        { time: 4, type: "submit_task", task: task("urgent", "a") },
        { time: 5, type: "worker_down", worker: "n" },
      ],
    });
    expect(tasks.map((t) => [t.task.id, t.submitted])).toEqual([
      ["a", false],
      ["b", false],
      ["c", false],
      ["urgent", true],
    ]);
    expect(graphEdges(tasks, ["a", "b", "c"])).toEqual([
      { id: "a->b", from: "a", to: "b", critical: true, submitted: false },
      { id: "b->c", from: "b", to: "c", critical: true, submitted: false },
      { id: "a->urgent", from: "a", to: "urgent", critical: false, submitted: true },
    ]);
  });
});
