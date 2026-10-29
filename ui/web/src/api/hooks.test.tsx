import { QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import type { ReactNode } from "react";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import { ApiError } from "./client";
import { createQueryClient, keys, useCreateRun, useLiveEvents, usePolicies, useRun } from "./hooks";

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function setup() {
  const client = createQueryClient();
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { client, wrapper };
}

const RUN = { run_id: "0123456789abcdef", result: { status: "ok" } };

describe("query hooks", () => {
  it("loads data through the client", async () => {
    server.use(http.get("/v1/policies", () => HttpResponse.json({ implemented: ["fifo"], all: ["fifo"] })));
    const { result } = renderHook(() => usePolicies(), { wrapper: setup().wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual({ implemented: ["fifo"], all: ["fifo"] });
  });

  it("does not retry a 4xx answer", async () => {
    let calls = 0;
    server.use(
      http.get("/v1/runs/:id", () => {
        calls += 1;
        return HttpResponse.json({ error: { code: "NOT_FOUND", message: "нет прогона" } }, { status: 404 });
      }),
    );
    const { result } = renderHook(() => useRun("missing"), { wrapper: setup().wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(ApiError);
    expect(calls).toBe(1);
  });

  it("puts a created run into the cache", async () => {
    server.use(http.post("/v1/runs", () => HttpResponse.json(RUN, { status: 201 })));
    const { client, wrapper } = setup();
    const { result } = renderHook(() => useCreateRun(), { wrapper });
    await result.current.mutateAsync({ problem: { tasks: [], cluster: { workers: [] } } });
    expect(client.getQueryData(keys.run(RUN.run_id))).toEqual(RUN);
  });
});

describe("useLiveEvents", () => {
  it("reads the journal past 10 000 records, only what is new, and once more when the run ends", async () => {
    let total = 12_000;
    const asked: number[] = [];
    server.use(
      http.get("/v1/runs/:id/events", ({ request }) => {
        const url = new URL(request.url);
        const from = Number(url.searchParams.get("from_seq"));
        const limit = Number(url.searchParams.get("limit"));
        asked.push(from);
        const to = Math.min(total, from + limit);
        const events = Array.from({ length: to - from }, (_, i) => ({ seq: from + i, t: 0, type: "READY" }));
        return HttpResponse.json({ events, total, next_seq: to < total ? to : null });
      }),
    );
    const { wrapper } = setup();
    const { result, rerender } = renderHook(({ running }) => useLiveEvents("0123456789abcdef", running), {
      wrapper,
      initialProps: { running: true },
    });
    await waitFor(() => expect(result.current.data?.length).toBe(12_000));
    asked.length = 0;
    total = 12_005; // прогон дописал журнал и закончился
    rerender({ running: false });
    await waitFor(() => expect(result.current.data?.length).toBe(12_005));
    expect(asked).toEqual([12_000]); // дочитан только хвост
    expect(result.current.data?.map((r) => r.seq).slice(-2)).toEqual([12_003, 12_004]);
  });
});
