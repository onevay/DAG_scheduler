import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import { createQueryClient } from "../api/hooks";
import type { Draft } from "../draft/draft";
import { DraftProvider } from "../draft/DraftContext";
import { BuilderPage } from "./BuilderPage";

const DRAFT: Draft = {
  tasks: [
    { id: "a", duration: 2, resources: { gpu: 1 } },
    { id: "b", duration: 3, resources: { gpu: 1 }, dependencies: ["a"] },
  ],
  cluster: { workers: [{ id: "n", resources: { gpu: 2 } }] },
};

let valid = true;
let posted: unknown;
const server = setupServer(
  http.get("/v1/scenarios", () => HttpResponse.json({ scenarios: [] })),
  http.get("/v1/policies", () =>
    HttpResponse.json({ policies: ["fifo", "priority_backfill"], defaults: { name: "priority_backfill" } }),
  ),
  http.post("/v1/validate", () =>
    HttpResponse.json(
      valid
        ? { valid: true, diagnostics: [], analysis: { lower_bound: 5, critical_path: ["a", "b"] } }
        : {
            valid: false,
            diagnostics: [
              { code: "E030", level: "error", detail: { task: "b" }, hint: "дедлайн недостижим" },
            ],
            analysis: null,
          },
    ),
  ),
  http.post("/v1/runs", async ({ request }) => {
    posted = await request.json();
    return HttpResponse.json({ run_id: "0123456789abcdef", result: {} }, { status: 201 });
  }),
);
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  server.resetHandlers();
  valid = true;
});
afterAll(() => server.close());

function renderPage() {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <DraftProvider initial={DRAFT}>
        <MemoryRouter initialEntries={["/builder"]}>
          <Routes>
            <Route path="/builder" element={<BuilderPage />} />
            <Route path="/runs/:id" element={<h1>прогон открыт</h1>} />
          </Routes>
        </MemoryRouter>
      </DraftProvider>
    </QueryClientProvider>,
  );
}

describe("BuilderPage", () => {
  it("runs a valid draft and opens the new run", async () => {
    renderPage();
    expect(await screen.findByText("Вход корректен")).toBeInTheDocument();
    expect(screen.getByText("Нижняя граница makespan — 5")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("radio", { name: "FIFO" }));
    await userEvent.click(screen.getByRole("radio", { name: "строгая" }));
    // Старение задают и выключают: пустое поле — null
    await userEvent.type(screen.getByLabelText("Старение очереди, ч на класс"), "5");
    await userEvent.clear(screen.getByLabelText("Старение очереди, ч на класс"));
    await userEvent.type(screen.getByLabelText("Вытеснять не раньше, чем через, ч"), "2");
    await userEvent.type(screen.getByLabelText("Прогноз перепланирования — до задач"), "0");
    await userEvent.click(screen.getByRole("button", { name: "Запустить" }));
    expect(await screen.findByRole("heading", { name: "прогон открыт" })).toBeInTheDocument();
    expect(posted).toMatchObject({
      problem: {
        tasks: DRAFT.tasks,
        policy: {
          name: "fifo",
          validation_mode: "strict",
          aging: null,
          min_runtime_before_preempt: 2,
          max_forecast_tasks: 0,
        },
      },
    });
  });

  it("blocks the run on errors and selects the task from the strip", async () => {
    valid = false;
    renderPage();
    expect(await screen.findByText("Ошибок: 1")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Запустить" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "b" }));
    expect(screen.getByLabelText("Задача")).toHaveValue("b");
  });
});
