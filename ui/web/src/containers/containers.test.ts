import { describe, expect, it } from "vitest";

import type { AgentInfo, Container, JournalRecord, Problem } from "../api/types";
import { attemptLog, expectation, p80Work, phase, readiness, wallTime } from "./containers";

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

describe("readiness", () => {
  const agent = (id: string, alive: boolean) => ({ id, alive }) as AgentInfo;
  const draft = (patch: Partial<Problem> = {}) =>
    ({
      name: "laptop",
      mode: "shadow",
      time_scale_ms: 100,
      tasks: [{ id: "a", duration: 1 }],
      cluster: { workers: [{ id: "w", resources: { cpu: 1 }, agent: "box" }] },
      ...patch,
    }) as Problem;

  it("blocks on what is missing and warns about agents that are not online", () => {
    const levels = (p: Problem, agents: AgentInfo[] = []) =>
      readiness(p, agents, null).map((c) => `${c.id}:${c.level}`);
    expect(levels(draft(), [agent("box", true)])).toEqual(["input:ok", "mode:ok", "nodes:ok"]);
    expect(levels(draft(), [agent("box", false)])).toContain("nodes:warn");
    expect(levels(draft({ mode: "simulate", tasks: [] }))).toEqual([
      "input:block",
      "mode:block",
      "nodes:warn",
    ]);
    const bare = draft({ cluster: { workers: [{ id: "w", resources: { cpu: 1 } }] } });
    expect(readiness(bare, [], null).find((c) => c.id === "nodes")?.text).toBe("Узлам нужен агент: w");
  });

  it("says how long the run will take on the wall clock", () => {
    expect(readiness(draft(), [], 600).at(-1)?.text).toBe(
      "Займёт не меньше 60 с: граница 600 единиц модели по 100 мс",
    );
    expect([wallTime(30, 1000), wallTime(30, 60_000), wallTime(600, 60_000)]).toEqual([
      "30 с",
      "30 мин",
      "10 ч",
    ]);
  });
});

describe("phase", () => {
  it("counts a container that exited with an error as killed, not done", () => {
    const c = (state: string, detail: string | null = null) => ({ state, detail }) as Container;
    expect([c("exited"), c("exited", "exit code 137"), c("oom"), c("started")].map(phase)).toEqual([
      "done",
      "killed",
      "killed",
      "running",
    ]);
  });
});
