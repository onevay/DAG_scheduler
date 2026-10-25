import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { MemoryRouter } from "react-router";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { createQueryClient } from "../api/hooks";
import { EMPTY_DRAFT, type Draft } from "../draft/draft";
import { DraftProvider } from "../draft/DraftContext";
import { plural, TASKS } from "../ui/plural";
import { ComparePage } from "./ComparePage";

const DRAFT: Draft = {
  name: "backfill",
  tasks: [{ id: "a", duration: 2 }],
  cluster: { workers: [{ id: "n", resources: { gpu: 4 } }] },
};

const METRICS: Record<string, [number, number]> = { fifo: [11, 1.25], priority: [15, 2.5] };
const server = setupServer(
  http.get("/v1/policies", () =>
    HttpResponse.json({ policies: ["fifo", "priority", "cpsat"], defaults: { name: "priority_backfill" } }),
  ),
  http.post("/v1/runs", async ({ request }) => {
    const { policy } = (await request.json()) as { policy: { name: string } };
    const m = METRICS[policy.name];
    if (!m)
      return HttpResponse.json(
        { error: { code: "POLICY_NOT_IMPLEMENTED", message: "нет решателя" } },
        { status: 400 },
      );
    const [makespan, wait] = m;
    return HttpResponse.json(
      {
        run_id: `run-${policy.name}`,
        result: {
          status: "OK",
          violations: [],
          metrics: {
            makespan,
            mean_wait: wait,
            utilization_gpu: 6.6 / makespan,
            lost_work_gpu: 0,
            deadline_misses: { critical: 0, high: 0, normal: 0, low: 0 },
          },
        },
      },
      { status: 201 },
    );
  }),
);
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());

function renderPage(draft: Draft) {
  render(
    <QueryClientProvider client={createQueryClient()}>
      <DraftProvider initial={draft}>
        <MemoryRouter>
          <ComparePage />
        </MemoryRouter>
      </DraftProvider>
    </QueryClientProvider>,
  );
}

describe("ComparePage", () => {
  it("runs every policy, ranks them by a metric and opens a run", async () => {
    renderPage(DRAFT);
    expect(
      await screen.findByText("Лучше всех — fifo (11 ч), хуже всех — priority (15 ч)."),
    ).toBeInTheDocument();
    expect(screen.getByText("backfill — 3 политики, детерминированно")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("cpsat: нет решателя");
    expect(screen.getByRole("button", { name: "fifo: 11 ч" })).toHaveAttribute("aria-pressed", "true");

    await userEvent.click(screen.getByRole("radio", { name: "Загрузка" }));
    expect(screen.getByRole("heading", { name: "Загрузка GPU" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "priority: 44%" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "priority: 44%" }));
    expect(
      screen.getByRole("img", { name: /^Профиль priority: makespan 73%, загрузка 73%, ожидание 50%/ }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Открыть прогон priority" })).toHaveAttribute(
      "href",
      "/runs/run-priority",
    );
  });

  it("asks for a draft when it is empty", () => {
    renderPage(EMPTY_DRAFT);
    expect(screen.getByText(/Черновик пуст/)).toBeInTheDocument();
  });
});

describe("plural", () => {
  it("picks the Russian form by number", () => {
    expect([1, 3, 5, 11, 21, 22].map((n) => plural(n, TASKS))).toEqual([
      "1 задача",
      "3 задачи",
      "5 задач",
      "11 задач",
      "21 задача",
      "22 задачи",
    ]);
  });
});
