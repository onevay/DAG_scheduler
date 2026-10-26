import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { createQueryClient } from "../api/hooks";
import { ExplainPage } from "./ExplainPage";

const link = (
  task: string,
  attempt: number,
  start: number,
  end: number,
  work: number,
  lost: number,
  cause: string,
  by?: string,
) => ({
  task,
  attempt,
  worker: "n",
  start,
  end,
  work,
  lost,
  cause,
  by,
});

const RESULT = {
  status: "OK",
  policy: "priority",
  metrics: { makespan: 6 },
  explain: {
    chain: [
      link("prep", 1, 0, 2, 2, 0, "origin"),
      link("train", 1, 2, 4, 1, 1, "dep", "prep"),
      link("train", 2, 4, 6, 2, 0, "fail", "train"),
    ],
    decomposition: { dep: 5, res: 0, lost: 1, release: 0, wait: 0 },
    idle: {
      by_code: { gpu: { IDLE_DOWN: 8, IDLE_NO_READY: 3, IDLE_POLICY: 0 } },
      intervals: [
        { worker: "n", from: 0, to: 2, code: "IDLE_NO_READY", free: { gpu: 1 }, detail: {} },
        { worker: "m", from: 2, to: 6, code: "IDLE_DOWN", free: { gpu: 2 }, detail: {} },
      ],
    },
  },
};

const server = setupServer(
  http.get("/v1/runs/:id", () => HttpResponse.json({ run_id: "r", result: RESULT })),
  http.get("/v1/runs/:id/problem", () =>
    HttpResponse.json({
      name: "failure",
      tasks: [],
      cluster: {
        workers: [
          { id: "n", resources: { gpu: 2 } },
          { id: "m", resources: { gpu: 2 } },
        ],
      },
    }),
  ),
);
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());

describe("ExplainPage", () => {
  it("decomposes makespan, explains chain steps and maps idle time", async () => {
    render(
      <QueryClientProvider client={createQueryClient()}>
        <MemoryRouter initialEntries={["/explain/r"]}>
          <Routes>
            <Route path="/explain/:runId" element={<ExplainPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findByRole("heading", { level: 1, name: "Почему 6 ч" })).toBeInTheDocument();
    expect(screen.getByText("= 5 РАБОТА ПО ЦЕПОЧКЕ")).toBeInTheDocument();
    expect(screen.getByText("+ 1 ПОТЕРЯ ПРИ ОТКАЗЕ")).toBeInTheDocument();

    // Сразу выбрано звено с потерей
    expect(screen.getByRole("button", { name: "train, 2–4, зависимость" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(
      screen.getByText("Старт в 2 — ровно когда закончилась prep. Сделано 1 ч, 1 ч потеряно."),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "train #2, 4–6, отказ" }));
    expect(
      screen.getByText(/^Попытка 2 стартует в 4 на n: прошлую попытку прервал отказ узла/),
    ).toBeInTheDocument();

    const idle = within(screen.getByRole("region", { name: "Почему простаивают GPU" }));
    expect(idle.getByText(/IDLE_DOWN: 8 из 11 GPU·ч/)).toBeInTheDocument();
    const cells = (worker: string) => [
      ...idle.getByRole("img", { name: `${worker}: простой по часам` }).children,
    ];
    expect(cells("n").map((c) => c.getAttribute("data-part"))).toEqual([
      "true",
      "true",
      "false",
      "false",
      "false",
      "false",
    ]);
    await userEvent.click(idle.getByRole("button", { name: "узел лежит" }));
    expect(cells("m").map((c) => c.getAttribute("data-dim"))).toEqual([
      "true",
      "true",
      "false",
      "false",
      "false",
      "false",
    ]);

    const codes = within(screen.getByRole("region", { name: "Простой, GPU·ч" }));
    expect(codes.getByText("11")).toBeInTheDocument();
    expect(codes.queryByText("IDLE_POLICY")).toBeNull();
  });
});
