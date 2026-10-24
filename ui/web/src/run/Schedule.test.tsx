import { QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { useState } from "react";
import { MemoryRouter } from "react-router";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { createQueryClient } from "../api/hooks";
import type { Problem, Result } from "../api/types";
import { Scrubber } from "./Moment";
import { Schedule } from "./Schedule";

const PROBLEM: Problem = {
  tasks: [
    { id: "train", duration: 6, kind: "train" },
    { id: "eval", duration: 2, kind: "eval", dependencies: ["train"] },
  ],
  cluster: {
    workers: [
      { id: "a", resources: { gpu: 4 } },
      { id: "b", resources: { gpu: 4 } },
    ],
  },
};

const RESULT = {
  status: "OK",
  policy: "fifo",
  violations: [],
  replans: [
    {
      seq: 3,
      t: 2,
      type: "REPLAN",
      trigger: "event",
      events: [{ type: "worker_down", worker: "a" }],
      changes: [{ task: "eval", old_start: 6, new_start: 7, delta: 1 }],
      new_tasks: {},
      dropped: [],
    },
  ],
  metrics: { makespan: 10 },
  attempts: [
    { task: "train", attempt: 1, worker: "a", start: 0, end: 2, resume_from: 0, saved: 1, outcome: "INT" },
    { task: "train", attempt: 2, worker: "b", start: 2, end: 8, resume_from: 1, saved: 0, outcome: "DONE" },
    { task: "eval", attempt: 1, worker: "b", start: 8, end: 10, resume_from: 0, saved: 0, outcome: "DONE" },
  ],
  tasks: {
    train: { status: "SUCCEEDED", start: 0, finish: 8, deadline: null, tardiness: null, waits: [] },
    eval: { status: "SUCCEEDED", start: 8, finish: 10, deadline: null, tardiness: null, waits: [] },
  },
} as unknown as Result;

const JOURNAL = [
  { seq: 0, t: 0, type: "START", task: "train", worker: "a", attempt: 1, resume_from: 0, reason: {} },
  { seq: 1, t: 2, type: "WORKER_DOWN", worker: "a" },
  {
    seq: 2,
    t: 2,
    type: "INTERRUPTED",
    task: "train",
    worker: "a",
    attempt: 1,
    saved: 1,
    lost: 1,
    remaining: 5,
    cause: "down",
  },
  { seq: 3, t: 2, type: "REPLAN", trigger: "event", events: [], changes: [], new_tasks: {}, dropped: [] },
  { seq: 4, t: 2, type: "START", task: "train", worker: "b", attempt: 2, resume_from: 1, reason: {} },
];

const server = setupServer(
  http.get("/v1/runs/:id/events", () =>
    HttpResponse.json({ events: JOURNAL, total: JOURNAL.length, next_seq: null }),
  ),
  http.get("/v1/runs/:id/explain", ({ request }) =>
    HttpResponse.json({
      chain: [],
      decomposition: {},
      attempts: [],
      idle: [],
      waits:
        new URL(request.url).searchParams.get("task") === "eval"
          ? [{ task: "eval", code: "WAIT_DEPS", from: 0, to: 8, detail: { pending: ["train"] } }]
          : [],
    }),
  ),
);
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());

function renderSchedule() {
  render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter>
        <Schedule runId="0123456789abcdef" result={RESULT} problem={PROBLEM} unit="ч" />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const bar = (name: RegExp) => screen.getByRole("button", { name });

describe("Schedule", () => {
  it("draws lanes and phases, jumps to a moment and shows the journal up to it", async () => {
    renderSchedule();
    expect(bar(/^train, попытка 1, 0–2 ч, прервана/)).toHaveAttribute("data-phase", "done");
    expect(bar(/^eval, попытка 1/)).toHaveAttribute("data-phase", "done");
    expect(await screen.findByText("недоступен 2–10")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "отказ 2" }));
    expect(screen.getByText("T = 2 Ч")).toBeInTheDocument();
    expect(bar(/^train, попытка 2/)).toHaveAttribute("data-phase", "now");
    expect(bar(/^eval, попытка 1/)).toHaveAttribute("data-phase", "future");
    const running = screen.getByText("Идут в момент 2 ч").parentElement as HTMLElement;
    expect(within(running).getByText("train · b")).toBeInTheDocument();
    expect(screen.getByText("train → b, попытка 2 с позиции 1")).toBeInTheDocument();
  });

  it("shows attempts and waits of the selected task", async () => {
    renderSchedule();
    const aside = within(screen.getByRole("complementary", { name: "Задача" }));
    await userEvent.click(bar(/^eval, попытка 1/));
    expect(bar(/^eval, попытка 1/)).toHaveAttribute("aria-pressed", "true");
    expect(await aside.findByText("WAIT_DEPS")).toBeInTheDocument();
    expect(aside.getByText("pending: train")).toBeInTheDocument();
    await userEvent.click(bar(/^train, попытка 1/));
    expect(aside.getByText("прервана")).toBeInTheDocument();
    expect(await aside.findByText("Не ждала: стартовала сразу.")).toBeInTheDocument();
  });
});

describe("Scrubber", () => {
  afterEach(() => vi.useRealTimers());

  it("plays to the end and stops", () => {
    vi.useFakeTimers();
    function Harness() {
      const [t, setT] = useState(10);
      return <Scrubber t={t} onChange={setT} horizon={10} events={[]} unit="ч" />;
    }
    render(<Harness />);
    // С конца проигрывание начинается заново
    fireEvent.click(screen.getByRole("button", { name: "Проиграть" }));
    expect(screen.getByText("T = 0 Ч")).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(120 * 44));
    expect(screen.getByText("T = 5 Ч")).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(120 * 50));
    expect(screen.getByText("T = 10 Ч")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Проиграть" })).toBeInTheDocument();
  });
});
