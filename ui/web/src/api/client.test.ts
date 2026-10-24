import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import { allEvents, api, ApiError } from "./client";

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

async function caught(promise: Promise<unknown>): Promise<ApiError> {
  const error = await promise.then(
    () => undefined,
    (e: unknown) => e,
  );
  expect(error).toBeInstanceOf(ApiError);
  return error as ApiError;
}

describe("request", () => {
  it("returns the parsed body and sends query and json body", async () => {
    let seen: { mode: string | null; type: string | null; body: unknown } | undefined;
    server.use(
      http.post("/v1/validate", async ({ request }) => {
        seen = {
          mode: new URL(request.url).searchParams.get("mode"),
          type: request.headers.get("content-type"),
          body: await request.json(),
        };
        return HttpResponse.json({ valid: true, diagnostics: [], analysis: null });
      }),
    );
    const problem = { tasks: [], cluster: { workers: [] } };
    const out = await api.validate(problem, "strict");
    expect(out.valid).toBe(true);
    expect(seen).toEqual({ mode: "strict", type: "application/json", body: problem });
  });

  it("turns an error body into ApiError with code, message and diagnostics", async () => {
    const diagnostic = { code: "CYCLE", severity: "error", message: "цикл", path: "tasks[0]" };
    server.use(
      http.post("/v1/runs", () =>
        HttpResponse.json(
          { error: { code: "INVALID_INPUT", message: "вход некорректен" }, diagnostics: [diagnostic] },
          { status: 422 },
        ),
      ),
    );
    const error = await caught(api.createRun({ tasks: [], cluster: { workers: [] } }));
    expect(error).toMatchObject({ status: 422, code: "INVALID_INPUT", message: "вход некорректен" });
    expect(error.diagnostics).toEqual([diagnostic]);
  });

  it("reports an unreachable API as NETWORK", async () => {
    server.use(http.get("/v1/policies", () => HttpResponse.error()));
    expect(await caught(api.policies())).toMatchObject({ status: 0, code: "NETWORK" });
  });

  it("reports a non-json answer as BAD_RESPONSE", async () => {
    server.use(http.get("/v1/policies", () => new HttpResponse("Bad Gateway", { status: 502 })));
    expect(await caught(api.policies())).toMatchObject({ status: 502, code: "BAD_RESPONSE" });
  });

  it("passes an abort through unchanged", async () => {
    server.use(http.get("/v1/policies", () => HttpResponse.json({})));
    const controller = new AbortController();
    controller.abort();
    await expect(api.policies(controller.signal)).rejects.not.toBeInstanceOf(ApiError);
  });

  it("drops undefined query values", async () => {
    let query = "";
    server.use(
      http.get("/v1/runs/:id/explain", ({ request }) => {
        query = new URL(request.url).search;
        return HttpResponse.json({ chain: [], decomposition: {}, attempts: [], waits: [], idle: [] });
      }),
    );
    await api.explain("abc", { task: "t1", worker: undefined });
    expect(query).toBe("?task=t1");
  });
});

describe("allEvents", () => {
  it("follows next_seq until the journal ends", async () => {
    const asked: string[] = [];
    server.use(
      http.get("/v1/runs/:id/events", ({ request }) => {
        const from = Number(new URL(request.url).searchParams.get("from_seq"));
        asked.push(`${from}`);
        const events = [{ seq: from }, { seq: from + 1 }];
        return HttpResponse.json({ events, total: 5, next_seq: from + 2 < 5 ? from + 2 : null });
      }),
    );
    const records = await allEvents("abc", undefined, 2);
    expect(asked).toEqual(["0", "2", "4"]);
    expect(records.map((r) => r.seq)).toEqual([0, 1, 2, 3, 4, 5]);
  });
});
