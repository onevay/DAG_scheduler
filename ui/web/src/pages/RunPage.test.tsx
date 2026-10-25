import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { createQueryClient } from "../api/hooks";
import { RunPage } from "./RunPage";

const RESULT = {
  status: "OK",
  policy: "priority",
  violations: [],
  replans: [
    {
      seq: 3,
      t: 2,
      type: "REPLAN",
      trigger: "event",
      events: [{ type: "worker_down", worker: "n" }],
      changes: [{ task: "a", old_start: 2, new_start: 3, delta: 1 }],
      new_tasks: {},
      dropped: [],
    },
  ],
  metrics: {
    makespan: 22,
    lower_bound: 21,
    gap_to_lb: 0.05,
    deadline_misses: { critical: 0, high: 1, normal: 0, low: 0 },
    weighted_tardiness: 0,
    utilization_gpu: 0.476,
    lost_work_gpu: 4,
    mean_wait: 0.0909,
  },
  attempts: [
    { task: "a", attempt: 1, worker: "n", start: 0, end: 2, resume_from: 0, saved: 0, outcome: "INT" },
    { task: "a", attempt: 2, worker: "n", start: 2, end: 6, resume_from: 0, saved: 0, outcome: "DONE" },
  ],
  tasks: {
    a: {
      status: "SUCCEEDED",
      start: 0,
      finish: 6,
      deadline: 5,
      tardiness: 1,
      waits: [{ code: "WAIT_RESOURCES", from: 0, to: 0, detail: {} }],
    },
  },
};

const JOURNAL = [
  { seq: 0, t: 0, type: "READY", task: "a" },
  { seq: 1, t: 0, type: "START", task: "a", worker: "n", attempt: 1, resume_from: 0, reason: {} },
];

const server = setupServer(
  http.get("/v1/runs/:id/events", () =>
    HttpResponse.json({ events: JOURNAL, total: JOURNAL.length, next_seq: null }),
  ),
  http.get("/v1/runs/:id", ({ params }) =>
    params.id === "missing"
      ? HttpResponse.json({ error: { code: "NOT_FOUND", message: "нет" } }, { status: 404 })
      : HttpResponse.json({ run_id: params.id, result: RESULT }),
  ),
  http.get("/v1/runs/:id/problem", () =>
    HttpResponse.json({
      name: "urgent_failure",
      tasks: [{ id: "a", duration: 4 }],
      cluster: { workers: [] },
    }),
  ),
);
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());

function renderAt(id: string, from?: string) {
  render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={[`/runs/${id}${from ? `?from=${from}` : ""}`]}>
        <Routes>
          <Route path="/runs/:runId" element={<RunPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("RunPage", () => {
  it("shows the header, metrics and tasks of a run", async () => {
    renderAt("0123456789abcdef");
    expect(await screen.findByRole("heading", { level: 1, name: "urgent_failure" })).toBeInTheDocument();
    expect(screen.getByText("OK · нарушений нет")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Почему 22, а не 21?" })).toHaveAttribute(
      "href",
      "/explain/0123456789abcdef",
    );
    const metric = (label: string) => screen.getByText(label).parentElement;
    expect(metric("Makespan")).toHaveTextContent("22 / 21 ч");
    expect(metric("Загрузка GPU")).toHaveTextContent("48 %");
    expect(metric("Потеряно")).toHaveTextContent("4 GPU·ч");
    expect(metric("Дедлайны")).toHaveTextContent("1 пропущено");
    expect(metric("Среднее ожидание")).toHaveTextContent("0.09 ч");
    const row = within(screen.getByRole("region", { name: "Задачи" })).getByRole("row", { name: /^a / });
    expect(row).toHaveTextContent("SUCCEEDED");
    expect(
      within(row)
        .getAllByRole("cell")
        .map((c) => c.textContent),
    ).toEqual(["SUCCEEDED", "2", "0", "6", "5", "1", "0", "WAIT_RESOURCES"]);
  });

  it("links the source run and lists replans", async () => {
    renderAt("child", "parent");
    expect(await screen.findByRole("link", { name: "parent" })).toHaveAttribute("href", "/runs/parent");
    const replans = within(screen.getByRole("region", { name: "Перепланирования" }));
    expect(replans.getByRole("row", { name: /^2 / })).toHaveTextContent("2отказ na +1——");
  });

  it("says when a run is missing", async () => {
    renderAt("missing");
    expect(await screen.findByRole("heading", { name: "Прогон не найден" })).toBeInTheDocument();
  });
});

// У Blob в jsdom нет text(): содержимое читается через FileReader
function readText(blob: Blob): Promise<string> {
  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.readAsText(blob);
  });
}

describe("downloads", () => {
  afterEach(() => vi.restoreAllMocks());

  it("saves the result and the journal as files", async () => {
    const saved: { name: string; text: Promise<string> }[] = [];
    let blob: Blob | undefined;
    vi.spyOn(URL, "createObjectURL").mockImplementation((b) => {
      blob = b as Blob;
      return "blob:x";
    });
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    vi.spyOn(HTMLElement.prototype, "click").mockImplementation(function (this: HTMLElement) {
      saved.push({ name: (this as HTMLAnchorElement).download, text: readText(blob as Blob) });
    });
    renderAt("0123456789abcdef");
    await userEvent.click(await screen.findByRole("button", { name: "Скачать result.json" }));
    await userEvent.click(await screen.findByRole("button", { name: "Скачать events.jsonl" }));
    expect(saved.map((f) => f.name)).toEqual([
      "urgent_failure-0123456789abcdef.result.json",
      "urgent_failure-0123456789abcdef.events.jsonl",
    ]);
    expect(JSON.parse(await (saved[0] as (typeof saved)[number]).text)).toMatchObject({ policy: "priority" });
    const lines = (await (saved[1] as (typeof saved)[number]).text).trimEnd().split("\n");
    expect(lines.map((l) => JSON.parse(l).seq)).toEqual([0, 1]);
  });
});
