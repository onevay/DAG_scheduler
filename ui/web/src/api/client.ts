import type {
  Diagnostic,
  ErrorBody,
  EventsPage,
  ExplainView,
  InputEvent,
  Intervention,
  JournalRecord,
  LiveRun,
  Logs,
  Agents,
  Live,
  LiveStarted,
  Policies,
  Policy,
  Problem,
  Replay,
  Run,
  RunAction,
  RunListItem,
  Scenario,
  Validation,
  WhatIf,
} from "./types";

// Пусто — тот же адрес, что у страницы: в разработке запросы проксирует Vite, в поставке API раздаёт интерфейс.
const BASE = import.meta.env.VITE_API_URL ?? "";

/** Ошибка API в его формате `{error: {code, message}, diagnostics?}`; NETWORK и BAD_RESPONSE — до разбора ответа. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly diagnostics: Diagnostic[] = [],
  ) {
    super(message);
    this.name = "ApiError";
  }
}

interface Options {
  method?: "GET" | "POST";
  query?: Record<string, string | number | undefined>;
  body?: unknown;
  signal?: AbortSignal;
}

export async function request<T>(
  path: string,
  { method = "GET", query, body, signal }: Options = {},
): Promise<T> {
  const url = new URL(path, BASE || window.location.origin);
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined) url.searchParams.set(key, String(value));
  }
  let response: Response;
  try {
    response = await fetch(url, {
      method,
      signal,
      headers: body === undefined ? undefined : { "content-type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (error) {
    if (signal?.aborted) throw error;
    throw new ApiError(0, "NETWORK", "API недоступен: запустите make api");
  }
  let data: unknown;
  try {
    data = await response.json();
  } catch {
    throw new ApiError(response.status, "BAD_RESPONSE", `API ответил ${response.status} не в формате JSON`);
  }
  if (!response.ok) {
    const error = (data as Partial<ErrorBody>).error;
    throw new ApiError(
      response.status,
      error?.code ?? "UNKNOWN",
      error?.message ?? `API ответил ${response.status}`,
      (data as Partial<ErrorBody>).diagnostics ?? [],
    );
  }
  return data as T;
}

export interface ExplainFilter {
  task?: string;
  worker?: string;
  t?: number;
}

export const api = {
  policies: (signal?: AbortSignal) => request<Policies>("/v1/policies", { signal }),
  scenarios: (signal?: AbortSignal) =>
    request<{ scenarios: Scenario[] }>("/v1/scenarios", { signal }).then((r) => r.scenarios),
  scenario: (name: string, signal?: AbortSignal) =>
    request<Problem>(`/v1/scenarios/${encodeURIComponent(name)}`, { signal }),
  validate: (problem: Problem, mode: "lenient" | "strict" = "lenient", signal?: AbortSignal) =>
    request<Validation>("/v1/validate", { method: "POST", query: { mode }, body: problem, signal }),
  createRun: (problem: Problem, policy?: Policy) =>
    request<Run>("/v1/runs", { method: "POST", body: { problem, policy } }),
  runs: (limit = 50, signal?: AbortSignal) =>
    request<{ runs: RunListItem[] }>("/v1/runs", { query: { limit }, signal }).then((r) => r.runs),
  run: (runId: string, signal?: AbortSignal) => request<Run>(`/v1/runs/${runId}`, { signal }),
  runProblem: (runId: string, signal?: AbortSignal) =>
    request<Problem>(`/v1/runs/${runId}/problem`, { signal }),
  eventsPage: (runId: string, fromSeq: number, limit: number, signal?: AbortSignal) =>
    request<EventsPage>(`/v1/runs/${runId}/events`, { query: { from_seq: fromSeq, limit }, signal }),
  explain: (runId: string, filter: ExplainFilter = {}, signal?: AbortSignal) =>
    request<ExplainView>(`/v1/runs/${runId}/explain`, { query: { ...filter }, signal }),
  /** Прогон на агентах (вход в режиме emulate или shadow): ответ 202 сразу, прогон идёт в фоне. */
  startOnAgents: (problem: Problem, policy?: Policy) =>
    request<LiveStarted>("/v1/runs", { method: "POST", body: { problem, policy } }),
  live: (runId: string, signal?: AbortSignal) => request<Live>(`/v1/runs/${runId}/containers`, { signal }),
  action: (runId: string, action: RunAction) =>
    request<Live>(`/v1/runs/${runId}/actions`, { method: "POST", body: action }),
  agents: (signal?: AbortSignal) => request<Agents>("/v1/agents", { signal }),
  liveRuns: (signal?: AbortSignal) =>
    request<{ runs: LiveRun[] }>("/v1/live", { signal }).then((r) => r.runs),
  logs: (runId: string, task: string, attempt: number, signal?: AbortSignal) =>
    request<Logs>(`/v1/runs/${runId}/containers/${encodeURIComponent(task)}/${attempt}/logs`, { signal }),
  replay: (runId: string) => request<Replay>(`/v1/runs/${runId}/replay`, { method: "POST" }),
  addEvent: (runId: string, event: InputEvent) =>
    request<Run>(`/v1/runs/${runId}/events`, { method: "POST", body: { event } }),
  whatIf: (problem: Problem, interventions: Intervention[], policy?: Policy) =>
    request<WhatIf>("/v1/whatif", { method: "POST", body: { problem, policy, interventions } }),
};

/** Весь журнал прогона, страница за страницей. */
export async function allEvents(runId: string, signal?: AbortSignal, page = 5000): Promise<JournalRecord[]> {
  const records: JournalRecord[] = [];
  let fromSeq: number | null | undefined = 0;
  while (fromSeq !== null && fromSeq !== undefined) {
    const next: EventsPage = await api.eventsPage(runId, fromSeq, page, signal);
    records.push(...next.events);
    fromSeq = next.next_seq;
  }
  return records;
}
