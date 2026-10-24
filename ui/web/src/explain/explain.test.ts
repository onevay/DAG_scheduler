import { describe, expect, it } from "vitest";

import { codeSummary, formula, idleGrid, type Link, stepText } from "./explain";

const link = (extra: Partial<Link>): Link => ({
  task: "t",
  attempt: 1,
  worker: "n",
  start: 2,
  end: 5,
  work: 3,
  lost: 0,
  cause: "dep",
  by: "p",
  ...extra,
});

describe("explain", () => {
  it("tells why each link started when it did", () => {
    expect(stepText(link({}), "ч")).toBe("Старт в 2 — ровно когда закончилась p. Работа — 3 ч.");
    expect(stepText(link({ cause: "fail", attempt: 2, work: 2, lost: 1 }), "ч")).toBe(
      "Попытка 2 стартует в 2 на n: прошлую попытку прервал отказ узла. Сделано 2 ч, 1 ч потеряно.",
    );
    expect(stepText(link({ cause: "origin", task: "prep", start: 0 }), "ч")).toMatch(/^prep стартует в 0/);
  });

  it("writes the makespan formula from non-zero parts", () => {
    expect(formula({ dep: 21, res: 0, lost: 1, release: 0, wait: 0 })).toEqual([
      "= 21 работа по цепочке",
      "+ 1 потеря при отказе",
    ]);
  });

  it("puts the widest idle reason into every hour and marks partial idle", () => {
    const grid = idleGrid(
      [
        { worker: "a", from: 0, to: 2, code: "IDLE_NO_READY", gpu: 4, detail: {} },
        { worker: "a", from: 1, to: 3, code: "IDLE_DOWN", gpu: 2, detail: {} },
        { worker: "b", from: 2, to: 3, code: "IDLE_RESERVED", gpu: 1, detail: {} },
      ],
      [
        { id: "a", gpu: 4 },
        { id: "b", gpu: 2 },
      ],
      4,
    );
    expect(
      grid.map((r) => [r.worker, r.cells.map((c) => (c.code ? `${c.code}${c.part ? "~" : ""}` : "-"))]),
    ).toEqual([
      ["a", ["IDLE_NO_READY", "IDLE_NO_READY", "IDLE_DOWN~", "-"]],
      ["b", ["-", "-", "IDLE_RESERVED~", "-"]],
    ]);
  });

  it("sums idle by code and names the main reason", () => {
    const { rows, total, insight } = codeSummary(
      { IDLE_DOWN: 40, IDLE_NO_READY: 89, IDLE_FRAGMENTATION: 0 },
      "ч",
    );
    expect(rows).toEqual([
      ["IDLE_NO_READY", 89],
      ["IDLE_DOWN", 40],
    ]);
    expect(total).toBe(129);
    expect(insight).toBe(
      "Больше всего простоя — IDLE_NO_READY: 89 из 129 GPU·ч (нет готовых задач, подходящих узлу).",
    );
    expect(codeSummary({ IDLE_DOWN: 0 }, "ч").insight).toBe("GPU не простаивали.");
  });
});
