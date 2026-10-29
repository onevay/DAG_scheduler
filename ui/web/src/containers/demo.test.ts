import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import { DEMO_AGENTS, startDemo, type Step } from "./demo";

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const agent = (id: string, alive: boolean) => ({ id, alive });

describe("startDemo", () => {
  it("starts both agents (one already running), waits for them and starts the example", async () => {
    const started: unknown[] = [];
    let polls = 0;
    server.use(
      http.post("/v1/local-agents", async ({ request }) => {
        const body = (await request.json()) as { id: string };
        started.push(body);
        return body.id === "demo-slow"
          ? HttpResponse.json({ error: { code: "AGENT_RUNNING", message: "already" } }, { status: 409 })
          : HttpResponse.json({ ...body, pid: 1, alive: true, exit_code: null, log: [] }, { status: 201 });
      }),
      http.get("/v1/agents", () => {
        polls += 1;
        return HttpResponse.json({
          agents: [agent("demo-fast", polls > 1), agent("demo-slow", true)],
          heartbeat_timeout_s: 15,
        });
      }),
      http.get("/v1/scenarios/chaos_demo", () => HttpResponse.json({ name: "chaos_demo", mode: "shadow" })),
      http.post("/v1/runs", () => HttpResponse.json({ run_id: "0123456789abcdef" }, { status: 202 })),
    );
    const steps: Step[] = [];
    const out = await startDemo((s) => steps.push(s), { everyMs: 1 });
    expect(started).toEqual(DEMO_AGENTS);
    expect(steps).toEqual(["agents", "waiting", "starting"]);
    expect(polls).toBe(2);
    expect(out).toMatchObject({ runId: "0123456789abcdef", problem: { name: "chaos_demo" } });
  });

  it("gives up when the agents never come online", async () => {
    server.use(
      http.post("/v1/local-agents", () => HttpResponse.json({}, { status: 201 })),
      http.get("/v1/agents", () => HttpResponse.json({ agents: [], heartbeat_timeout_s: 15 })),
    );
    await expect(startDemo(() => {}, { timeoutMs: 3, everyMs: 1 })).rejects.toThrow(/не вышли на связь/);
  });
});
