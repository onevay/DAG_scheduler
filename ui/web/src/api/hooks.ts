import { QueryClient, useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";

import { allEvents, api, ApiError, type ExplainFilter } from "./client";
import type { InputEvent, Intervention, Live, Policy, PolicyName, Problem, Run, RunAction } from "./types";

export const keys = {
  policies: ["policies"] as const,
  scenarios: ["scenarios"] as const,
  runs: (limit: number) => ["runs", limit] as const,
  run: (runId: string) => ["run", runId] as const,
  runProblem: (runId: string) => ["run", runId, "problem"] as const,
  runEvents: (runId: string) => ["run", runId, "events"] as const,
  explain: (runId: string, filter: ExplainFilter) => ["run", runId, "explain", filter] as const,
  validation: (problem: Problem, mode: string) => ["validation", mode, problem] as const,
  live: (runId: string) => ["live", runId] as const,
  liveEvents: (runId: string) => ["live", runId, "events"] as const,
};

// Прогон по run_id не меняется никогда: однажды загруженный, он не перезапрашивается.
const IMMUTABLE = { staleTime: Infinity, gcTime: 30 * 60 * 1000 };

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        refetchOnWindowFocus: false,
        // Ошибки запроса (4xx) повтором не исправить; сеть и 5xx — повторить пару раз.
        retry: (failures, error) =>
          !(error instanceof ApiError && error.status >= 400 && error.status < 500) && failures < 2,
      },
    },
  });
}

export const usePolicies = () =>
  useQuery({ queryKey: keys.policies, queryFn: ({ signal }) => api.policies(signal), staleTime: Infinity });

export const useScenarios = () =>
  useQuery({ queryKey: keys.scenarios, queryFn: ({ signal }) => api.scenarios(signal), staleTime: Infinity });

export const useRuns = (limit = 50) =>
  useQuery({ queryKey: keys.runs(limit), queryFn: ({ signal }) => api.runs(limit, signal) });

export const useRun = (runId: string) =>
  useQuery({ queryKey: keys.run(runId), queryFn: ({ signal }) => api.run(runId, signal), ...IMMUTABLE });

export const useRunProblem = (runId: string) =>
  useQuery({
    queryKey: keys.runProblem(runId),
    queryFn: ({ signal }) => api.runProblem(runId, signal),
    ...IMMUTABLE,
  });

export const useRunEvents = (runId: string) =>
  useQuery({
    queryKey: keys.runEvents(runId),
    queryFn: ({ signal }) => allEvents(runId, signal),
    ...IMMUTABLE,
  });

export const useExplain = (runId: string, filter: ExplainFilter = {}, enabled = true) =>
  useQuery({
    queryKey: keys.explain(runId, filter),
    queryFn: ({ signal }) => api.explain(runId, filter, signal),
    enabled,
    ...IMMUTABLE,
  });

/** Проверка входа; новый запрос — при каждом изменении `problem`, прежний ответ виден, пока идёт новый. */
export const useValidation = (problem: Problem | undefined, mode: "lenient" | "strict" = "lenient") =>
  useQuery({
    queryKey: keys.validation(problem ?? ({} as Problem), mode),
    queryFn: ({ signal }) => api.validate(problem as Problem, mode, signal),
    enabled: problem !== undefined,
    placeholderData: (previous) => previous,
  });

/** Новый прогон сразу кладётся в кеш: страница прогона откроется без второго запроса. */
function useRunMutation<V>(start: (vars: V) => Promise<Run>) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: start,
    onSuccess: (run) => {
      client.setQueryData(keys.run(run.run_id), run);
      void client.invalidateQueries({ queryKey: ["runs"] });
    },
  });
}

export const useCreateRun = () =>
  useRunMutation(({ problem, policy }: { problem: Problem; policy?: Policy }) =>
    api.createRun(problem, policy),
  );

/** Запуск на агентах: ответ не прогон, а его id — состояние смотрят на экране «Контейнеры». */
export const useStartOnAgents = () => {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ problem, policy }: { problem: Problem; policy?: Policy }) =>
      api.startOnAgents(problem, policy),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["runs"] }),
  });
};

/** Прогон на агентах сейчас; пока идёт — раз в секунду. */
export const useLive = (runId: string | undefined) =>
  useQuery({
    queryKey: keys.live(runId ?? ""),
    queryFn: ({ signal }) => api.live(runId as string, signal),
    enabled: runId !== undefined,
    refetchInterval: (query) => (query.state.data?.status === "running" ? 1000 : false),
  });

/** Журнал идущего прогона: обновляется вместе с его состоянием. */
export const useLiveEvents = (runId: string | undefined, running: boolean) =>
  useQuery({
    queryKey: keys.liveEvents(runId ?? ""),
    queryFn: ({ signal }) => api.eventsPage(runId as string, 0, 10_000, signal).then((p) => p.events),
    enabled: runId !== undefined,
    refetchInterval: running ? 1000 : false,
  });

/** Действие над прогоном на агентах; ответ — его новое состояние. */
export const useRunAction = (runId: string) => {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (action: RunAction) => api.action(runId, action),
    onSuccess: (live: Live) => client.setQueryData(keys.live(runId), live),
  });
};

/** Проигрывание сохранённого прогона теми же фактами: те же ли решения принимает политика. */
export const useReplay = (runId: string) => useMutation({ mutationFn: () => api.replay(runId) });

export const useAddEvent = () =>
  useRunMutation(({ runId, event }: { runId: string; event: InputEvent }) => api.addEvent(runId, event));

/**
 * Прогоны одного входа под каждой политикой. Запрос POST, но повторять его безопасно: run_id — хеш входа и
 * политики, сохранённый прогон API отдаёт без пересчёта.
 */
export const usePolicyRuns = (problem: Problem | undefined, names: readonly PolicyName[]) =>
  useQueries({
    queries: names.map((name) => ({
      queryKey: ["compare", name, problem] as const,
      queryFn: () => api.createRun(problem as Problem, { ...problem?.policy, name }),
      enabled: problem !== undefined,
      staleTime: Infinity,
    })),
  });

export const useWhatIf = () => {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({
      problem,
      interventions,
      policy,
    }: {
      problem: Problem;
      interventions: Intervention[];
      policy?: Policy;
    }) => api.whatIf(problem, interventions, policy),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["runs"] }),
  });
};
