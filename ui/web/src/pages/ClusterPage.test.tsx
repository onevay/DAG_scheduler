import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { createQueryClient } from "../api/hooks";
import type { Draft } from "../draft/draft";
import { DraftProvider, useDraft } from "../draft/DraftContext";
import { ClusterPage } from "./ClusterPage";

const DRAFT: Draft = {
  tasks: [{ id: "a", duration: 4, resources: { gpu: 2 } }],
  cluster: {
    workers: [
      { id: "node-A", resources: { gpu: 4 } },
      { id: "node-B", resources: { gpu: 2 }, labels: { zone: "eu" } },
    ],
  },
};

const server = setupServer(
  http.post("/v1/validate", () =>
    HttpResponse.json({ valid: true, diagnostics: [], analysis: { lower_bound: 20, critical_path: ["a"] } }),
  ),
);
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());

let latest: Draft = DRAFT;
function Spy() {
  latest = useDraft().draft;
  return null;
}

function renderPage() {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <DraftProvider initial={DRAFT}>
        <ClusterPage />
        <Spy />
      </DraftProvider>
    </QueryClientProvider>,
  );
}

const card = (id: string) => within(screen.getByRole("region", { name: id }));

describe("ClusterPage", () => {
  it("drops a node for a window and restores it", async () => {
    renderPage();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Кластер · 6 GPU");
    // Шкала — 24 ч, когда проверка вернёт нижнюю границу 20; до того — 4 ч
    await card("node-A").findByRole("button", { name: "Уронить 6–12 ч" });
    await userEvent.click(card("node-A").getByRole("button", { name: "Уронить 6–12 ч" }));
    expect(latest.events).toEqual([
      { time: 6, type: "worker_down", worker: "node-A" },
      { time: 12, type: "worker_up", worker: "node-A" },
    ]);
    expect(card("node-A").getByText("падает 6–12 ч")).toBeInTheDocument();
    expect(screen.getByText("6 Ч")).toBeInTheDocument();
    expect(screen.getByText("24 GPU·Ч")).toBeInTheDocument();
    await userEvent.click(card("node-A").getByRole("button", { name: "Убрать отказы" }));
    expect(latest.events).toEqual([]);
  });

  it("edits GPU and labels, adds and removes nodes", async () => {
    renderPage();
    await userEvent.click(card("node-B").getByRole("button", { name: "Больше GPU" }));
    await userEvent.click(card("node-B").getByRole("button", { name: "Убрать метку zone=eu" }));
    await userEvent.type(card("node-B").getByLabelText("Новая метка узла node-B"), "gpu=a100{Enter}");
    expect(latest.cluster.workers[1]).toEqual({
      id: "node-B",
      resources: { gpu: 3 },
      labels: { gpu: "a100" },
    });
    await userEvent.click(screen.getByRole("button", { name: "Новый узел" }));
    expect(latest.cluster.workers.map((w) => w.id)).toEqual(["node-A", "node-B", "node_1"]);
    await userEvent.click(card("node-A").getByRole("button", { name: "Удалить узел" }));
    expect(latest.cluster.workers.map((w) => w.id)).toEqual(["node-B", "node_1"]);
  });

  it("adds, retimes and removes events from the list", async () => {
    renderPage();
    await userEvent.selectOptions(screen.getByLabelText("Тип события"), "submit_task");
    const time = screen.getByLabelText("Момент нового события, ч");
    await userEvent.clear(time);
    await userEvent.type(time, "9");
    await userEvent.click(screen.getByRole("button", { name: "Добавить" }));
    expect(latest.events).toEqual([
      {
        time: 9,
        type: "submit_task",
        task: { id: "urgent_1", duration: 1, resources: { gpu: 1 }, priority: 10 },
      },
    ]);
    expect(screen.getByText("urgent_1 — priority 10")).toBeInTheDocument();

    const moment = screen.getByLabelText("Момент события подача, ч");
    await userEvent.clear(moment);
    await userEvent.type(moment, "3{Enter}");
    expect(latest.events?.[0]?.time).toBe(3);
    await userEvent.click(screen.getByRole("button", { name: "Удалить событие подача в 3 ч" }));
    expect(latest.events).toEqual([]);
  });
});
