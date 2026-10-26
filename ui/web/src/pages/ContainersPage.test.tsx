import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import { createQueryClient } from "../api/hooks";
import type { Container } from "../api/types";
import {
  attemptLog,
  counts,
  phase,
  scaleLabel,
  share,
  stateLabel,
  withoutAgent,
} from "../containers/containers";
import { type Draft } from "../draft/draft";
import { DraftProvider } from "../draft/DraftContext";
import { ContainersPage } from "./ContainersPage";

const RUN = "0123456789abcdef";
const container = (task: string, state: string, fields: Partial<Container> = {}): Container => ({
  task,
  attempt: 1,
  worker: "laptop",
  agent: "laptop",
  state,
  container_id: null,
  resume_from: 0,
  progress: 0,
  detail: null,
  ...fields,
});
const LIVE = {
  run_id: RUN,
  status: "running",
  now: 37,
  time_scale_ms: 1000,
  workers: { laptop: { agent: "laptop", up: true, dropped: false } },
  containers: [
    container("load_data", "exited", { progress: 10 }),
    container("embed_index", "oom", { progress: 10 }),
    container("train_small", "checkpoint", { progress: 20, container_id: "3f2a" }),
  ],
};
const PROBLEM = {
  time_unit: "minute",
  cluster: { workers: [{ id: "laptop", resources: { cpu: 8 }, agent: "laptop" }] },
  tasks: [
    { id: "load_data", duration: 10 },
    { id: "embed_index", duration: 25 },
    { id: "train_small", duration: 40 },
  ],
};
const EVENTS = [
  {
    seq: 0,
    t: 35,
    type: "START",
    task: "train_small",
    worker: "laptop",
    attempt: 1,
    resume_from: 0,
    reason: {},
  },
];
const posted: unknown[] = [];
const server = setupServer(
  http.get(`/v1/runs/${RUN}/containers`, () => HttpResponse.json(LIVE)),
  http.get(`/v1/runs/${RUN}/problem`, () => HttpResponse.json(PROBLEM)),
  http.get(`/v1/runs/${RUN}/events`, () => HttpResponse.json({ events: EVENTS, total: 1, next_seq: null })),
  http.post(`/v1/runs/${RUN}/actions`, async ({ request }) => {
    const action = (await request.json()) as { kind: string };
    posted.push(action);
    const dropped = action.kind === "worker_down";
    return HttpResponse.json({ ...LIVE, workers: { laptop: { agent: "laptop", up: !dropped, dropped } } });
  }),
  http.post("/v1/runs", async ({ request }) => {
    posted.push(await request.json());
    return HttpResponse.json({ run_id: RUN, status: "running" }, { status: 202 });
  }),
);
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => (posted.length = 0));
afterAll(() => server.close());

function renderAt(path: string, draft: Draft) {
  render(
    <QueryClientProvider client={createQueryClient()}>
      <DraftProvider initial={draft}>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route path="/containers" element={<ContainersPage />} />
            <Route path="/containers/:runId" element={<ContainersPage />} />
          </Routes>
        </MemoryRouter>
      </DraftProvider>
    </QueryClientProvider>,
  );
}

describe("containers", () => {
  it("sorts containers into phases and measures saved work", () => {
    const [done, oom, running] = LIVE.containers as [Container, Container, Container];
    expect([done, oom, running].map(phase)).toEqual(["done", "killed", "running"]);
    expect(counts(LIVE.containers)).toEqual({ all: 3, running: 1, waiting: 0, killed: 1, done: 1 });
    expect([share(done, 10), share(running, 40), share(oom, undefined)]).toEqual([1, 0.5, 0]);
    expect(stateLabel(oom)).toBe("убит: не хватило памяти");
    expect([scaleLabel(100), scaleLabel(5000), scaleLabel(60_000)]).toEqual(["100 мс", "5 с", "1 мин"]);
    expect(attemptLog(EVENTS as never, running, "мин")).toEqual(["35 мин · старт на laptop с позиции 0"]);
    expect(withoutAgent({ ...PROBLEM, cluster: { workers: [{ id: "a", resources: {} }] } } as never)).toEqual(
      ["a"],
    );
  });
});

describe("ContainersPage", () => {
  it("shows containers of a running run, filters them and acts on the selected one", async () => {
    renderAt(`/containers/${RUN}`, PROBLEM as Draft);
    expect(await screen.findByText("идёт · момент модели 37")).toBeInTheDocument();
    await screen.findByRole("button", {
      name: /^train_small, попытка 1 на laptop: идёт, чекпоинт сохранён, 50%$/,
    });
    expect(screen.getByRole("button", { name: "убиты · 1" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "завершены · 1" }));
    expect(screen.queryByRole("button", { name: /^train_small/ })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "все · 3" }));
    await userEvent.click(await screen.findByRole("button", { name: /^train_small/ }));
    const panel = screen.getByRole("region", { name: "Выбранный контейнер · TRAIN_SMALL" });
    expect(within(panel).getByText("3f2a")).toBeInTheDocument();
    expect(await within(panel).findByText("35 мин · старт на laptop с позиции 0")).toBeInTheDocument();
    await userEvent.click(within(panel).getByRole("button", { name: "Сохранить чекпоинт" }));
    await userEvent.click(within(panel).getByRole("button", { name: "Уронить узел laptop" }));
    expect(posted).toEqual([
      { kind: "checkpoint", task: "train_small" },
      { kind: "worker_down", worker: "laptop" },
    ]);
    expect(await within(panel).findByRole("button", { name: "Вернуть узел laptop" })).toBeInTheDocument();
  });

  it("chooses the mode, gives nodes an agent and starts a run on agents", async () => {
    const draft = { ...PROBLEM, cluster: { workers: [{ id: "laptop", resources: { cpu: 8 } }] } } as Draft;
    renderAt("/containers", draft);
    await userEvent.click(screen.getByRole("radio", { name: "Shadow" }));
    expect(screen.getByText(/рядом считается план/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("radio", { name: "100 мс" }));
    expect(screen.getByRole("button", { name: "Запустить на агентах" })).toBeDisabled();
    expect(screen.getByText("Без агента: laptop")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Назначить узлам" }));
    await userEvent.click(screen.getByRole("button", { name: "Запустить на агентах" }));
    expect(posted).toEqual([
      {
        problem: expect.objectContaining({
          mode: "shadow",
          time_scale_ms: 100,
          cluster: { workers: [{ id: "laptop", resources: { cpu: 8 }, agent: "laptop" }] },
        }),
      },
    ]);
    expect(await screen.findByText("идёт · момент модели 37")).toBeInTheDocument();
  });
});
