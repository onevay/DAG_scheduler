import { useState } from "react";

import { ApiError } from "../api/client";
import { useWhatIf } from "../api/hooks";
import type { Intervention, PolicyName, Problem } from "../api/types";
import { freeId } from "../draft/draft";
import { ButtonLink } from "../ui/controls";
import { plural, VARIANTS } from "../ui/plural";
import styles from "./WhatIf.module.css";
import { deltaNote, describe, headline, type Kind, KINDS, largest, suggest } from "./interventions";

const MAX = 16;

/** «Что если…»: набор вмешательств во вход, один прогон на каждое и разница с исходным под той же политикой. */
export function WhatIf({
  problem,
  policy,
  unit,
}: {
  problem: Problem;
  policy: PolicyName | null;
  unit: string;
}) {
  const [list, setList] = useState<Intervention[]>(() => suggest(problem));
  const whatIf = useWhatIf();
  const base = whatIf.data?.baseline;
  const run = () =>
    whatIf.mutate({
      problem,
      interventions: list,
      policy: policy ? { ...problem.policy, name: policy } : undefined,
    });

  return (
    <section aria-labelledby="whatif-title" className={styles.board}>
      <div className={styles.head}>
        <div className={styles.stack}>
          <h2 id="whatif-title" className={styles.tech}>
            ЧТО ЕСЛИ…
          </h2>
          <span className={styles.caption}>
            Одно изменение входа — один прогон
            {policy && `, политика ${policy}`}
            {base?.metrics && `, исходный makespan ${base.metrics.makespan} ${unit}`}
          </span>
        </div>
        <span className={styles.badge}>разница — только от изменения, без шума</span>
      </div>

      <ul className={styles.chips} aria-label="Вмешательства">
        {list.map((i, n) => (
          <li key={n}>
            <button
              type="button"
              className={styles.chip}
              aria-label={`Убрать: ${describe(i, unit)}`}
              onClick={() => setList(list.filter((_, k) => k !== n))}
            >
              {describe(i, unit)} ×
            </button>
          </li>
        ))}
      </ul>
      {list.length < MAX && <AddIntervention problem={problem} onAdd={(i) => setList([...list, i])} />}
      <div className={styles.row}>
        <button
          type="button"
          className={styles.run}
          disabled={list.length === 0 || whatIf.isPending}
          onClick={run}
        >
          {whatIf.isPending ? "Считаю варианты…" : `Посчитать ${plural(list.length, VARIANTS)}`}
        </button>
        {whatIf.isError && (
          <span role="alert" className={styles.error}>
            {whatIf.error.message}
            {whatIf.error instanceof ApiError &&
              whatIf.error.diagnostics.map((d) => ` · ${d.code} ${d.hint}`)}
          </span>
        )}
      </div>

      {whatIf.data && (
        <div className={styles.cards}>
          {whatIf.data.variants.map((v) => {
            const head = headline(v.delta, unit);
            return (
              <article key={v.run_id} className={`${styles.card} motion-lift`}>
                <span className={styles.title}>{describe(v.intervention as Intervention, unit)}</span>
                <span className={styles.result}>
                  <span className={styles.value}>
                    {v.metrics ? `${v.metrics.makespan} ${unit.toUpperCase()}` : v.status}
                  </span>
                  {head && (
                    <span className={styles.delta} data-tone={head.tone}>
                      {head.text}
                    </span>
                  )}
                </span>
                <span className={styles.note}>{deltaNote(v.delta, unit)}</span>
                <ButtonLink to={`/runs/${v.run_id}?from=${whatIf.data.baseline.run_id}`} variant="accent">
                  Открыть вариант
                </ButtonLink>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}

/** Новое вмешательство: вид и его параметры; задачи и узлы — из входа. */
function AddIntervention({ problem, onAdd }: { problem: Problem; onAdd: (i: Intervention) => void }) {
  const tasks = [
    ...problem.tasks.map((t) => t.id),
    ...(problem.events ?? []).flatMap((e) => e.task?.id ?? []),
  ];
  const workers = problem.cluster.workers.map((w) => w.id);
  // Новый узел — копия самого крупного
  const largestWorker = problem.cluster.workers.length > 0 ? largest(problem.cluster.workers) : undefined;
  const [kind, setKind] = useState<Kind>("set_checkpoint");
  const [task, setTask] = useState(tasks[0] ?? "");
  const [worker, setWorker] = useState(workers[0] ?? "");
  const [number, setNumber] = useState("");
  const [preemptible, setPreemptible] = useState(true);

  const value = number === "" ? null : Math.max(0, Math.round(Number(number)));
  const build = (): Intervention | null => {
    switch (kind) {
      case "add_worker":
        return largestWorker
          ? { kind, worker: { id: freeId("node", workers), resources: { ...largestWorker.resources } } }
          : null;
      case "remove_failures":
        return worker ? { kind, worker } : null;
      case "set_checkpoint":
        return task ? { kind, task, checkpoint_interval: value && value > 0 ? value : null } : null;
      case "set_preemptible":
        return task ? { kind, task, preemptible } : null;
      case "set_priority":
        return task ? { kind, task, priority: Math.min(10, value ?? 10) } : null;
      case "set_deadline":
        return task ? { kind, task, deadline: value } : null;
    }
  };
  const next = build();
  const numberLabel = {
    set_checkpoint: "каждые (пусто — без)",
    set_priority: "приоритет 0–10 (10)",
    set_deadline: "момент (пусто — без)",
  }[kind as string];

  return (
    <div role="group" aria-label="Новое вмешательство" className={styles.row}>
      <select aria-label="Вид вмешательства" value={kind} onChange={(e) => setKind(e.target.value as Kind)}>
        {KINDS.map((k) => (
          <option key={k.value} value={k.value}>
            {k.label}
          </option>
        ))}
      </select>
      {kind === "remove_failures" ? (
        <select aria-label="Узел" value={worker} onChange={(e) => setWorker(e.target.value)}>
          {workers.map((w) => (
            <option key={w}>{w}</option>
          ))}
        </select>
      ) : (
        kind !== "add_worker" && (
          <select aria-label="Задача" value={task} onChange={(e) => setTask(e.target.value)}>
            {tasks.map((t) => (
              <option key={t}>{t}</option>
            ))}
          </select>
        )
      )}
      {kind === "set_preemptible" && (
        <select
          aria-label="Вытеснять"
          value={String(preemptible)}
          onChange={(e) => setPreemptible(e.target.value === "true")}
        >
          <option value="true">можно вытеснять</option>
          <option value="false">нельзя вытеснять</option>
        </select>
      )}
      {numberLabel && (
        <input
          type="number"
          min={0}
          aria-label={numberLabel}
          placeholder={numberLabel}
          value={number}
          onChange={(e) => setNumber(e.target.value)}
        />
      )}
      <button type="button" className={styles.add} disabled={!next} onClick={() => next && onAdd(next)}>
        Добавить
      </button>
    </div>
  );
}
