import { Link } from "react-router";

import type { AgentInfo, Live, Problem } from "../api/types";
import { formatResources } from "../ui/resources";
import type { Check, Progress } from "./containers";
import { MODES } from "./containers";
import styles from "./Containers.module.css";
import type { Summary } from "./failureFeed";

const MARK = { ok: "✓", warn: "!", block: "✗" } as const;

/** Путь прогона на агентах тремя шагами; шаг отмечен, когда для него всё готово. */
export function Steps({ agents, setup, run }: { agents: boolean; setup: boolean; run: boolean }) {
  const steps = [
    {
      done: agents,
      title: "Агенты",
      text: "процессы на машинах с контейнерами: они запускают попытки задач",
    },
    { done: setup, title: "Прогон", text: "вход, режим и масштаб времени; у каждого узла — свой агент" },
    { done: run, title: "Итог", text: "факт против плана: сколько заняло, что упало и как восстановилось" },
  ];
  return (
    <ol className={styles.steps} aria-label="Шаги прогона на агентах">
      {steps.map((s, i) => (
        <li key={s.title} className={styles.step} data-done={s.done}>
          <span className={styles.stepMark} aria-hidden="true">
            {s.done ? "✓" : i + 1}
          </span>
          <span>
            <strong>{s.title}</strong> — {s.text}
          </span>
        </li>
      ))}
    </ol>
  );
}

/** Что готово к запуску и что мешает; каждый пункт — одной строкой. */
export function Checklist({ checks }: { checks: readonly Check[] }) {
  return (
    <ul className={styles.checks} aria-label="Готовность к запуску">
      {checks.map((c) => (
        <li key={c.id} className={styles.check} data-level={c.level}>
          <span className={styles.checkMark} aria-hidden="true">
            {MARK[c.level]}
          </span>
          {c.text}
        </li>
      ))}
    </ul>
  );
}

const GOALS = {
  emulate: "выполнить расписание политики на настоящих контейнерах",
  shadow: "выполнить расписание на контейнерах и сравнить факт с планом той же политики",
} as const;

/** Сводка идущего или законченного прогона: зачем он, как идёт, чем кончился, и чьи агенты его ведут. */
export function Overview({
  live,
  problem,
  progress,
  failures,
  agents,
  unit,
}: {
  live: Live;
  problem: Problem | undefined;
  progress: Progress;
  failures: Summary;
  agents: readonly AgentInfo[];
  unit: string;
}) {
  const mode = live.shadow ? "shadow" : "emulate"; // у прогона shadow рядом всегда есть план
  const policy = problem?.policy?.name ?? "—";
  const share = progress.total ? progress.done / progress.total : 0;
  const plan = live.shadow?.plan_makespan;
  const ids = [...new Set(Object.values(live.workers).map((w) => w.agent))].sort();
  const running = live.status === "running";
  const outcome = running
    ? null
    : live.status === "cancelled"
      ? `Прогон отменён на моменте ${live.now} ${unit}: закончено ${progress.done} из ${progress.total} задач.`
      : `Прогон закончен за ${live.now} ${unit}${plan !== undefined ? ` при плане ${plan} ${unit}` : ""}: ` +
        `${progress.done} из ${progress.total} задач выполнены` +
        (failures.total
          ? `, сбоев ${failures.total}, задач после сбоя восстановлено ${failures.recovered}` +
            (failures.failed ? `, не выполнено ${failures.failed}` : "")
          : ", без сбоев") +
        ".";
  return (
    <div className={styles.overview}>
      <p className={styles.goal}>
        <span className={styles.caption}>
          {MODES[mode].label} · политика {policy}
        </span>
        Цель — {GOALS[mode]}.
      </p>
      <div
        role="progressbar"
        aria-label="Задачи выполнены"
        aria-valuemin={0}
        aria-valuemax={progress.total}
        aria-valuenow={progress.done}
        className={styles.bar}
      >
        <div className={styles.fill} style={{ width: `${Math.round(share * 100)}%` }} />
      </div>
      <dl className={styles.stats}>
        <div>
          <dt>Задачи</dt>
          <dd>
            {progress.done} из {progress.total}
          </dd>
        </div>
        <div>
          <dt>Идут сейчас</dt>
          <dd>{progress.running}</dd>
        </div>
        <div>
          <dt>Время модели</dt>
          <dd>
            {live.now} {unit}
            {plan !== undefined && ` · план ${plan}`}
          </dd>
        </div>
        <div>
          <dt>Сбои</dt>
          <dd>{failures.total ? `${failures.total} · задач восстановлено ${failures.recovered}` : "нет"}</dd>
        </div>
      </dl>
      {outcome && (
        <p className={styles.outcome} role="status">
          {outcome}
        </p>
      )}
      <ul className={styles.agentLine} aria-label="Агенты">
        {ids.map((id) => {
          const a = agents.find((x) => x.id === id);
          const state = a?.alive ? "на связи" : "нет связи";
          return (
            <li key={id} aria-label={`Агент ${id}: ${state}`} data-alive={Boolean(a?.alive)}>
              <span className={styles.dot} aria-hidden="true" />
              {id}
              {a && (
                <span className={styles.caption}>
                  {" "}
                  — {a.runtime}, {formatResources(a.capacity)}
                </span>
              )}
            </li>
          );
        })}
      </ul>
      {!running && live.status === "done" && (
        <Link to={`/runs/${live.run_id}`} className={styles.next}>
          Открыть прогон
        </Link>
      )}
    </div>
  );
}
