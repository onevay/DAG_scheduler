import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { createQueryClient } from "../api/hooks";
import { App } from "../App";
import { EMPTY_DRAFT, type Draft } from "../draft/draft";
import { DraftProvider, useDraft } from "../draft/DraftContext";
import { OverviewPage } from "./OverviewPage";

const item = (run_id: string, name: string, makespan: number) => ({
  run_id,
  name,
  policy: "fifo",
  status: "OK",
  tasks: 2,
  makespan,
  created_at: "2026-10-24T10:00:00.000Z",
});

const result = (makespan: number) => ({
  status: "OK",
  policy: "fifo",
  violations: [],
  metrics: {
    makespan,
    lower_bound: 4,
    utilization_gpu: 0.5,
    lost_work_gpu: 1,
    mean_wait: 0,
    deadline_misses: { critical: 0, high: 0, normal: 1, low: 0 },
  },
  attempts: [
    { task: "a", attempt: 1, worker: "n", start: 0, end: 2, resume_from: 0, saved: 0, outcome: "DONE" },
  ],
  tasks: {},
  explain: {
    idle: {
      by_code: {},
      intervals: [{ worker: "n", from: 2, to: 4, code: "IDLE_DOWN", gpu: 2, detail: {} }],
    },
  },
});

const server = setupServer(
  http.get("/v1/runs", () => HttpResponse.json({ runs: [item("r1", "first", 4), item("r2", "second", 5)] })),
  http.get("/v1/runs/:id", ({ params }) =>
    HttpResponse.json({ run_id: params.id, result: result(params.id === "r1" ? 4 : 5) }),
  ),
  http.get("/v1/runs/:id/problem", () =>
    HttpResponse.json({
      tasks: [{ id: "a", duration: 2, resources: { gpu: 2 } }],
      cluster: { workers: [{ id: "n", resources: { gpu: 2 } }] },
    }),
  ),
  http.get("/v1/runs/:id/events", () => HttpResponse.json({ events: [], total: 0, next_seq: null })),
  http.get("/v1/scenarios", () =>
    HttpResponse.json({
      scenarios: [{ name: "demo", tasks: 11, workers: 3, events: 0, policy: "fifo", time_unit: "hour" }],
    }),
  ),
  http.get("/v1/scenarios/demo", () =>
    HttpResponse.json({ name: "demo", tasks: [{ id: "x", duration: 1 }], cluster: { workers: [] } }),
  ),
  http.get("/v1/policies", () => HttpResponse.json({ policies: ["fifo"], defaults: {} })),
);
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());

function Where() {
  const { pathname } = useLocation();
  const { draft } = useDraft();
  return <output data-testid="where">{`${pathname} ${draft.tasks.map((t) => t.id).join(",")}`}</output>;
}

function renderAt(path: string, ui: React.ReactNode, draft: Draft = EMPTY_DRAFT) {
  render(
    <QueryClientProvider client={createQueryClient()}>
      <DraftProvider initial={draft}>
        <MemoryRouter initialEntries={[path]}>
          {ui}
          <Where />
        </MemoryRouter>
      </DraftProvider>
    </QueryClientProvider>,
  );
}

describe("OverviewPage", () => {
  it("shows the latest run, switches runs and maps node load", async () => {
    renderAt("/", <OverviewPage />);
    const runs = within(screen.getByRole("region", { name: "Прогоны" }));
    expect(await runs.findByRole("button", { name: /^first/ })).toHaveAttribute("aria-pressed", "true");
    expect(await screen.findByText("= граница · граница 4")).toBeInTheDocument();
    expect(screen.getByText("пропущено дедлайнов 1 · потеряно 1 GPU·ч")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "n по часам: занят 2, недоступен 2" })).toBeInTheDocument();
    expect(screen.getByText("50%")).toBeInTheDocument();

    await userEvent.click(runs.getByRole("button", { name: /^second/ }));
    expect(await screen.findByText("+25% · граница 4")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Открыть расписание/ })).toHaveAttribute("href", "/runs/r2");
  });

  it("starts a new scenario from an example", async () => {
    renderAt(
      "/",
      <Routes>
        <Route path="/" element={<OverviewPage />} />
        <Route path="/builder" element={<h1>сборка</h1>} />
      </Routes>,
    );
    await userEvent.click(await screen.findByRole("button", { name: /^demo/ }));
    expect(await screen.findByText("/builder x")).toBeInTheDocument();
  });

  it("opens the latest run from a section without id", async () => {
    renderAt("/explain", <App />);
    expect(await screen.findByText("/explain/r1")).toBeInTheDocument();
  });
});
