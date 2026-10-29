import { api, ApiError } from "../api/client";
import type { LocalAgentStart, Problem } from "../api/types";

/** Демо сбоев: пример с двумя машинами разной скорости и хаосом и два локальных агента fake под его узлы. */
export const DEMO_SCENARIO = "chaos_demo";
export const DEMO_AGENTS: readonly LocalAgentStart[] = [
  { id: "demo-fast", runtime: "fake", cpu: 16, ram_mb: 65536, gpu: 2 },
  { id: "demo-slow", runtime: "fake", cpu: 16, ram_mb: 65536, gpu: 2 },
];

export type Step = "agents" | "waiting" | "starting";
export const STEPS: Record<Step, string> = {
  agents: "запускаю двух агентов fake…",
  waiting: "жду, пока агенты выйдут на связь…",
  starting: "запускаю прогон…",
};

const pause = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** Агенты (уже идущие — не ошибка), их выход на связь, пример и прогон на них. Возвращает вход и id прогона. */
export async function startDemo(
  onStep: (step: Step) => void,
  { timeoutMs = 20_000, everyMs = 500 } = {},
): Promise<{ problem: Problem; runId: string }> {
  onStep("agents");
  for (const agent of DEMO_AGENTS) {
    try {
      await api.startLocalAgent(agent);
    } catch (error) {
      if (!(error instanceof ApiError && error.code === "AGENT_RUNNING")) throw error;
    }
  }
  onStep("waiting");
  const ids = new Set(DEMO_AGENTS.map((a) => a.id));
  for (let waited = 0; ; waited += everyMs) {
    const { agents } = await api.agents();
    if (agents.filter((a) => ids.has(a.id) && a.alive).length === ids.size) break;
    if (waited >= timeoutMs) throw new Error("агенты демо не вышли на связь — посмотрите их журнал ниже");
    await pause(everyMs);
  }
  onStep("starting");
  const problem = await api.scenario(DEMO_SCENARIO);
  const started = await api.startOnAgents(problem);
  return { problem, runId: started.run_id };
}
