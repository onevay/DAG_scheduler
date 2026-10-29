import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { createQueryClient } from "../api/hooks";
import type { Container } from "../api/types";
import {
  attemptLog,
  counts,
  phase,
  scaleLabel,
  share,
  stateLabel,
  usageMeters,
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
    container("train_small", "checkpoint", {
      progress: 20,
      container_id: "3f2a",
      usage: { cpu_milli: 1500, ram_mb: 950 },
    }),
  ],
};
const PROBLEM = {
  time_unit: "minute",
  cluster: { workers: [{ id: "laptop", resources: { cpu: 8 }, agent: "laptop" }] },
  tasks: [
    { id: "load_data", duration: 10 },
    { id: "embed_index", duration: 25 },
    { id: "train_small", duration: 40, resources: { cpu: 2, ram_mb: 1024 } },
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
const AGENTS = {
  heartbeat_timeout_s: 15,
  agents: [
    {
      id: "laptop",
      runtime: "docker",
      version: "0.1.0",
      capacity: { cpu: 7, ram_mb: 14336 },
      max_containers: 4,
      alive: true,
      seen_s_ago: 0.4,
      running: [`${RUN}:train_small:1`],
    },
    {
      id: "beta",
      runtime: "fake",
      version: "0.1.0",
      capacity: { cpu: 4 },
      max_containers: 4,
      alive: false,
      seen_s_ago: 42,
      running: [],
    },
  ],
};
const LIVE_RUNS = [
  { run_id: RUN, name: "laptop", mode: "shadow", status: "running", now: 37, containers: 3, running: 1 },
];
const LOCALS = [
  {
    id: "laptop",
    runtime: "docker",
    pid: 42,
    alive: true,
    exit_code: null,
    log: ["agent laptop: runtime docker"],
  },
];
const posted: unknown[] = [];
const deleted: string[] = [];
let checkFails = false;
const server = setupServer(
  http.get("/v1/agents", () => HttpResponse.json(AGENTS)),
  http.get("/v1/live", () => HttpResponse.json({ runs: LIVE_RUNS })),
  http.get("/v1/local-agents", () => HttpResponse.json({ agents: LOCALS })),
  http.post("/v1/local-agents", async ({ request }) => {
    const body = (await request.json()) as { id: string; runtime: string };
    posted.push(body);
    if (checkFails)
      return HttpResponse.json(
        {
          error: { code: "AGENT_NOT_READY", message: "ok   runtime\nFAIL runtime docker: no workload image" },
        },
        { status: 422 },
      );
    return HttpResponse.json({ ...body, pid: 7, alive: true, exit_code: null, log: [] }, { status: 201 });
  }),
  http.delete("/v1/local-agents/:id", ({ params }) => {
    deleted.push(String(params.id));
    return new HttpResponse(null, { status: 204 });
  }),
  http.get(`/v1/runs/${RUN}/containers`, () => HttpResponse.json(LIVE)),
  http.get(`/v1/runs/${RUN}/problem`, () => HttpResponse.json(PROBLEM)),
  http.get(`/v1/runs/${RUN}/containers/train_small/1/logs`, () =>
    HttpResponse.json({ lines: ["start: 0 of 40 units", "checkpoint: 20 units"] }),
  ),
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
afterEach(() => {
  posted.length = 0;
  deleted.length = 0;
  checkFails = false;
});
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
    expect(usageMeters({ cpu_milli: 1500, ram_mb: 950 }, { cpu: 2, ram_mb: 1024 })).toEqual([
      { label: "CPU", text: "1,5 из 2 ядер", share: 0.75, near: false },
      { label: "ОЗУ", text: "950 МБ из 1 ГБ", share: 950 / 1024, near: true },
    ]);
    expect(usageMeters({ cpu_milli: 300, ram_mb: 64 }, undefined).map((m) => [m.text, m.share])).toEqual([
      ["0,3 ядер", null],
      ["64 МБ", null],
    ]);
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

  it("shows the usage of the selected container against its limits and the tail of its log", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    renderAt(`/containers/${RUN}`, PROBLEM as Draft);
    const panel = await screen.findByRole("region", { name: "Выбранный контейнер · TRAIN_SMALL" });
    expect(await within(panel).findByRole("meter", { name: "CPU: 1,5 из 2 ядер" })).toHaveAttribute(
      "aria-valuenow",
      "75",
    );
    expect(within(panel).getByRole("meter", { name: "ОЗУ: 950 МБ из 1 ГБ" })).toHaveAttribute(
      "aria-valuenow",
      "93",
    );
    expect(within(panel).getByText(/у предела памяти/)).toBeInTheDocument();
    const log = await within(panel).findByRole("region", { name: "Журнал контейнера" });
    expect(log).toHaveTextContent("start: 0 of 40 units checkpoint: 20 units");
    expect(within(panel).getByText("Журнал контейнера · 2 строки")).toBeInTheDocument();
    const pause = within(panel).getByRole("button", { name: "Пауза" });
    await userEvent.click(pause);
    expect(within(panel).getByRole("button", { name: "Продолжить" })).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(within(panel).getByRole("button", { name: "Копировать" }));
    expect(writeText).toHaveBeenCalledWith("start: 0 of 40 units\ncheckpoint: 20 units");
    expect(await within(panel).findByRole("button", { name: "Скопировано" })).toBeInTheDocument();
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

  it("lists agents and runs on agents, and gives each node an agent the server knows", async () => {
    const draft = {
      ...PROBLEM,
      mode: "emulate",
      cluster: {
        workers: [
          { id: "laptop", resources: { cpu: 8 } },
          { id: "spare", resources: { cpu: 2 } },
        ],
      },
    } as Draft;
    renderAt("/containers", draft);
    const agents = await screen.findByRole("list", { name: "Агенты" });
    const laptop = within(agents).getByRole("listitem", { name: "Агент laptop: на связи" });
    expect(laptop).toHaveTextContent("docker");
    expect(laptop).toHaveTextContent("7 CPU · 14 ГБ");
    expect(laptop).toHaveTextContent("1 из 4");
    expect(within(agents).getByRole("listitem", { name: "Агент beta: нет связи" })).toHaveTextContent(
      "нет связи · 42 с",
    );
    expect(await screen.findByRole("link", { name: "laptop" })).toHaveAttribute("href", `/containers/${RUN}`);
    expect(screen.getByText(/Shadow · идёт · момент модели 37 · идут 1 из 3/)).toBeInTheDocument();

    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Агент узла laptop" }), "laptop");
    const spare = screen.getByRole("combobox", { name: "Агент узла spare" });
    expect(within(spare).getByRole("option", { name: "beta — нет связи" })).toBeInTheDocument();
    await userEvent.selectOptions(spare, "beta");
    await userEvent.click(screen.getByRole("button", { name: "Запустить на агентах" }));
    expect(posted).toEqual([
      {
        problem: expect.objectContaining({
          cluster: {
            workers: [
              { id: "laptop", resources: { cpu: 8 }, agent: "laptop" },
              { id: "spare", resources: { cpu: 2 }, agent: "beta" },
            ],
          },
        }),
      },
    ]);
  });

  it("starts an agent on the server's machine and stops a local one", async () => {
    renderAt("/containers", PROBLEM as Draft);
    const agents = await screen.findByRole("list", { name: "Агенты" });
    const laptop = await within(agents).findByRole("listitem", { name: "Агент laptop: на связи" });
    expect(laptop).toHaveTextContent("на этой машине · pid 42");
    await userEvent.click(within(laptop).getByText("Журнал процесса"));
    expect(within(laptop).getByText("agent laptop: runtime docker")).toBeVisible();
    await userEvent.click(within(laptop).getByRole("button", { name: "Остановить агента laptop" }));
    expect(deleted).toEqual(["laptop"]);

    const form = screen.getByRole("form", { name: "Запустить агента на этой машине" });
    expect(within(form).getByRole("textbox", { name: "Id нового агента" })).toHaveAttribute(
      "placeholder",
      "local-1",
    );
    await userEvent.click(within(form).getByRole("radio", { name: "Без контейнеров" }));
    await userEvent.click(within(form).getByRole("switch", { name: /Ёмкость автоматически/ }));
    await userEvent.click(within(form).getByRole("button", { name: "Больше ядер агента" }));
    await userEvent.click(within(form).getByRole("button", { name: "Запустить local-1" }));
    expect(posted).toEqual([{ id: "local-1", runtime: "fake", cpu: 5, ram_mb: 8192 }]);

    checkFails = true;
    await userEvent.type(within(form).getByRole("textbox", { name: "Id нового агента" }), "gpu-box");
    await userEvent.click(within(form).getByRole("radio", { name: "Docker" }));
    await userEvent.click(within(form).getByRole("button", { name: "Запустить gpu-box" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent(
      "FAIL runtime docker: no workload image",
    );
  });
});
