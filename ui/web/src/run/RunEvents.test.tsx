import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import { createQueryClient } from "../api/hooks";
import type { Problem } from "../api/types";
import { RunEvents } from "./RunEvents";

const PROBLEM: Problem = {
  tasks: [{ id: "a", duration: 4 }],
  cluster: {
    workers: [
      { id: "n1", resources: { gpu: 4 } },
      { id: "n2", resources: { gpu: 2 } },
    ],
  },
  events: [{ time: 1, type: "submit_task", task: { id: "urgent_1", duration: 1 } }],
};

let posted: { run: string; event: unknown }[] = [];
let reject = false;
const server = setupServer(
  http.post("/v1/runs/:id/events", async ({ params, request }) => {
    if (reject)
      return HttpResponse.json(
        {
          error: { code: "INVALID_INPUT", message: "вход некорректен" },
          diagnostics: [{ code: "E040", level: "error", detail: {}, hint: "узла нет" }],
        },
        { status: 422 },
      );
    const { event } = (await request.json()) as { event: unknown };
    posted.push({ run: String(params.id), event });
    return HttpResponse.json({ run_id: `run${posted.length}`, result: {} }, { status: 201 });
  }),
);
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  posted = [];
  reject = false;
});
afterAll(() => server.close());

function Where() {
  const location = useLocation();
  return <output data-testid="where">{location.pathname + location.search}</output>;
}

function renderAt(t: number) {
  render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={["/runs/base"]}>
        <Routes>
          <Route path="/runs/:id" element={<RunEvents runId="base" problem={PROBLEM} t={t} unit="ч" />} />
        </Routes>
        <Where />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("RunEvents", () => {
  it("drops a node at the moment and brings it back in a second run", async () => {
    renderAt(4.6);
    expect(screen.getByText("А если в момент 5 ч…")).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByRole("combobox"), "n2");
    await userEvent.click(screen.getByRole("button", { name: "Позже" }));
    await userEvent.click(screen.getByRole("button", { name: "Позже" }));
    await userEvent.click(screen.getByRole("button", { name: "Пересчитать прогон" }));
    expect(await screen.findByText("/runs/run2?from=base")).toBeInTheDocument();
    expect(posted).toEqual([
      { run: "base", event: { time: 5, type: "worker_down", worker: "n2" } },
      { run: "run1", event: { time: 7, type: "worker_up", worker: "n2" } },
    ]);
  });

  it("submits an urgent task with a free id", async () => {
    renderAt(3);
    await userEvent.click(screen.getByRole("radio", { name: "подать задачу" }));
    await userEvent.click(screen.getByRole("button", { name: "Дольше" }));
    await userEvent.type(screen.getByPlaceholderText("нет"), "9");
    await userEvent.click(screen.getByRole("button", { name: "Пересчитать прогон" }));
    expect(await screen.findByText("/runs/run1?from=base")).toBeInTheDocument();
    expect(posted[0]?.event).toEqual({
      time: 3,
      type: "submit_task",
      task: { id: "urgent_2", duration: 3, resources: { gpu: 1 }, priority: 10, deadline: 9 },
    });
  });

  it("shows why the API refused the event", async () => {
    reject = true;
    renderAt(2);
    await userEvent.click(screen.getByRole("button", { name: "Пересчитать прогон" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("вход некорректенE040 узла нет");
    expect(screen.getByTestId("where")).toHaveTextContent("/runs/base");
  });
});
