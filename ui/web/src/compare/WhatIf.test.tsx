import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { MemoryRouter } from "react-router";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import { createQueryClient } from "../api/hooks";
import type { Problem } from "../api/types";
import { WhatIf } from "./WhatIf";

const PROBLEM: Problem = {
  tasks: [{ id: "a", duration: 1 }],
  cluster: { workers: [{ id: "n", resources: { gpu: 2 } }] },
  events: [{ time: 1, type: "worker_down", worker: "n" }],
};

let body: { interventions: unknown[]; policy?: { name: string } } | undefined;
let reject = false;
const server = setupServer(
  http.post("/v1/whatif", async ({ request }) => {
    body = (await request.json()) as typeof body;
    if (reject)
      return HttpResponse.json(
        { error: { code: "INVALID_REQUEST", message: "no task 'x'" } },
        { status: 422 },
      );
    return HttpResponse.json({
      baseline: { run_id: "base", status: "OK", metrics: { makespan: 5 } },
      variants: (body?.interventions ?? []).map((intervention, i) => ({
        run_id: `v${i}`,
        status: "OK",
        intervention,
        metrics: { makespan: 4 + i },
        delta: { makespan: i - 1, lost_work_gpu: i === 0 ? -2 : 0 },
      })),
    });
  }),
);
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  body = undefined;
  reject = false;
});
afterAll(() => server.close());

function renderWhatIf() {
  render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter>
        <WhatIf problem={PROBLEM} policy="fifo" unit="ч" />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("WhatIf", () => {
  it("starts from suggestions, lets edit the list and shows variants with deltas", async () => {
    renderWhatIf();
    expect(screen.getAllByRole("button", { name: /^Убрать: / }).map((b) => b.textContent)).toEqual([
      "Без отказов n ×",
      "Ещё узел node_1 на 2 GPU ×",
    ]);
    await userEvent.click(screen.getByRole("button", { name: "Убрать: Ещё узел node_1 на 2 GPU" }));
    await userEvent.selectOptions(screen.getByLabelText("Вид вмешательства"), "set_priority");
    await userEvent.type(screen.getByLabelText("приоритет 0–10 (10)"), "7");
    await userEvent.click(screen.getByRole("button", { name: "Добавить" }));
    await userEvent.click(screen.getByRole("button", { name: "Посчитать 2 варианта" }));

    expect(await screen.findByText("−1 ч")).toBeInTheDocument();
    expect(body).toEqual({
      problem: PROBLEM,
      policy: { name: "fifo" },
      interventions: [
        { kind: "remove_failures", worker: "n" },
        { kind: "set_priority", task: "a", priority: 7 },
      ],
    });
    expect(screen.getByText("потери −2 GPU·ч")).toBeInTheDocument();
    expect(screen.getByText("±0 ч")).toBeInTheDocument();
    expect(screen.getByText(/исходный makespan 5 ч/)).toBeInTheDocument();
    expect(
      screen.getAllByRole("link", { name: "Открыть вариант" }).map((a) => a.getAttribute("href")),
    ).toEqual(["/runs/v0?from=base", "/runs/v1?from=base"]);
  });

  it("shows why the API refused", async () => {
    reject = true;
    renderWhatIf();
    await userEvent.click(screen.getByRole("button", { name: "Посчитать 2 варианта" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("no task 'x'");
  });
});
