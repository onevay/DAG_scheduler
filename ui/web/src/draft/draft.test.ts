import { describe, expect, it } from "vitest";

import type { Diagnostic } from "../api/types";
import {
  type Draft,
  type DraftAction,
  diagnosticsByTask,
  draftReducer,
  EMPTY_DRAFT,
  freeId,
  loadDraft,
  parseDraft,
  saveDraft,
} from "./draft";

const BASE: Draft = {
  tasks: [
    { id: "prep", duration: 2 },
    { id: "train", duration: 6, dependencies: ["prep"] },
    { id: "eval", duration: 1, dependencies: ["train", "prep"] },
  ],
  cluster: { workers: [{ id: "node-A", resources: { gpu: 4 } }] },
};

const run = (...actions: DraftAction[]) => actions.reduce(draftReducer, BASE);
const deps = (draft: Draft) => Object.fromEntries(draft.tasks.map((t) => [t.id, t.dependencies ?? []]));

describe("draftReducer", () => {
  it("adds tasks with free ids and defaults", () => {
    const draft = run({ type: "addTask" }, { type: "addTask", task: { kind: "train" } });
    expect(draft.tasks.slice(3)).toEqual([
      { id: "task_1", duration: 1, resources: { gpu: 1 } },
      { id: "task_2", duration: 1, resources: { gpu: 1 }, kind: "train" },
    ]);
    expect(freeId("task", ["task_1", "task_3"])).toBe("task_2");
  });

  it("renaming a task rewrites dependencies on it", () => {
    const draft = run({ type: "updateTask", id: "prep", patch: { id: "load", duration: 3 } });
    expect(draft.tasks[0]).toEqual({ id: "load", duration: 3 });
    expect(deps(draft)).toEqual({ load: [], train: ["load"], eval: ["train", "load"] });
  });

  it("removing a task drops edges to it", () => {
    expect(deps(run({ type: "removeTask", id: "prep" }))).toEqual({ train: [], eval: ["train"] });
  });

  it("links without self-loops or duplicates and unlinks", () => {
    const linked = run(
      { type: "link", from: "prep", to: "train" },
      { type: "link", from: "eval", to: "eval" },
      { type: "link", from: "train", to: "prep" },
    );
    expect(deps(linked)).toEqual({ prep: ["train"], train: ["prep"], eval: ["train", "prep"] });
    expect(deps(draftReducer(linked, { type: "unlink", from: "prep", to: "eval" })).eval).toEqual(["train"]);
  });

  it("removing a worker removes its events", () => {
    const draft = run(
      { type: "addWorker" },
      { type: "addEvent", event: { time: 5, type: "worker_down", worker: "node_1" } },
      { type: "addEvent", event: { time: 2, type: "worker_down", worker: "node-A" } },
      { type: "addEvent", event: { time: 9, type: "worker_up", worker: "node_1" } },
      { type: "removeWorker", id: "node_1" },
    );
    expect(draft.cluster.workers.map((w) => w.id)).toEqual(["node-A"]);
    expect(draft.events).toEqual([{ time: 2, type: "worker_down", worker: "node-A" }]);
  });

  it("keeps events sorted by time and removes by index", () => {
    const draft = run(
      { type: "addEvent", event: { time: 7, type: "worker_down", worker: "node-A" } },
      { type: "addEvent", event: { time: 3, type: "worker_down", worker: "node-A" } },
      { type: "removeEvent", index: 1 },
    );
    expect(draft.events?.map((e) => e.time)).toEqual([3]);
  });

  it("merges policy fields and clears an empty name", () => {
    const draft = run(
      { type: "setPolicy", patch: { name: "fifo" } },
      { type: "setPolicy", patch: { aging: 10 } },
      { type: "setName", name: "" },
    );
    expect(draft.policy).toEqual({ name: "fifo", aging: 10 });
    expect(draft.name).toBeNull();
  });
});

describe("parseDraft", () => {
  it("accepts problem.json and rejects anything else", () => {
    expect(parseDraft(JSON.stringify(BASE))).toEqual(BASE);
    expect(() => parseDraft("{")).toThrow("Файл не в формате JSON");
    expect(() => parseDraft('{"tasks": []}')).toThrow("нужны tasks и cluster.workers");
  });
});

describe("storage", () => {
  function memory(): Storage {
    const data = new Map<string, string>();
    return {
      get length() {
        return data.size;
      },
      clear: () => data.clear(),
      getItem: (key) => data.get(key) ?? null,
      key: (i) => [...data.keys()][i] ?? null,
      removeItem: (key) => void data.delete(key),
      setItem: (key, value) => void data.set(key, value),
    };
  }

  it("round-trips the draft", () => {
    const storage = memory();
    saveDraft(BASE, storage);
    expect(loadDraft(storage)).toEqual(BASE);
  });

  it("falls back to an empty draft on missing or broken data", () => {
    const storage = memory();
    expect(loadDraft(storage)).toBe(EMPTY_DRAFT);
    storage.setItem("expsched.draft.v1", "not json");
    expect(loadDraft(storage)).toBe(EMPTY_DRAFT);
  });

  it("survives a storage that throws", () => {
    const broken = {
      ...memory(),
      setItem: () => {
        throw new Error("quota");
      },
    };
    expect(() => saveDraft(BASE, broken)).not.toThrow();
  });
});

describe("diagnosticsByTask", () => {
  const diag = (code: string, detail: Record<string, unknown>): Diagnostic => ({
    code,
    level: "error",
    detail,
    hint: "",
  });

  it("groups by task, cycle members and schema location", () => {
    const groups = diagnosticsByTask(BASE, [
      diag("E030", { task: "eval" }),
      diag("E012", { cycle: ["prep", "train", "prep"] }),
      diag("E070", { loc: "tasks.1.duration" }),
      diag("E050", {}),
    ]);
    expect(Object.fromEntries([...groups].map(([task, items]) => [task, items.map((d) => d.code)]))).toEqual({
      eval: ["E030"],
      prep: ["E012"],
      train: ["E012", "E070"],
      "": ["E050"],
    });
  });
});
