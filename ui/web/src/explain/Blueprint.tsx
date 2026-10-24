import { useState } from "react";

import type { Result } from "../api/types";
import styles from "./Explain.module.css";
import { causeLabel, formula, PARTS, stepText } from "./explain";

/** Makespan на «чертеже»: из чего сложился, звенья критической цепочки и почему каждое стартовало тогда. */
export function Blueprint({
  explain,
  makespan,
  unit,
}: {
  explain: NonNullable<Result["explain"]>;
  makespan: number;
  unit: string;
}) {
  const { chain, decomposition } = explain;
  // По умолчанию — первое звено, у которого что-то потеряно, иначе первое: там обычно и ответ на «почему»
  const [picked, setPicked] = useState(() =>
    Math.max(
      0,
      chain.findIndex((l) => l.lost > 0),
    ),
  );
  const current = chain[picked];
  const U = unit.toUpperCase();

  return (
    <section aria-labelledby="explain-blueprint" className={styles.blueprint}>
      <h2 id="explain-blueprint" className={styles.hidden}>
        Разложение makespan
      </h2>
      <div className={styles.top}>
        <div className={styles.total}>
          <span className={styles.huge}>
            {makespan} {U}
          </span>
          <span className={styles.formula}>
            {formula(decomposition).map((line) => (
              <span key={line}>{line.toUpperCase()}</span>
            ))}
          </span>
        </div>
        <dl className={styles.parts}>
          {PARTS.map((p) => (
            <div key={p.key}>
              <dt>
                <span className={styles.swatch} style={{ background: p.color }} />
                {p.label}
              </dt>
              <dd>
                {decomposition[p.key]} {U}
              </dd>
            </div>
          ))}
        </dl>
      </div>

      <div className={styles.strip} aria-hidden="true">
        {chain.flatMap((l, i) => [
          <span
            key={`w${i}`}
            className={styles.segment}
            data-on={i === picked}
            style={{ flexGrow: l.work }}
          />,
          ...(l.lost > 0
            ? [
                <span
                  key={`l${i}`}
                  className={`${styles.segment} ${styles.lost}`}
                  style={{ flexGrow: l.lost }}
                />,
              ]
            : []),
        ])}
      </div>

      <div className={styles.scroll}>
        <div className={styles.steps}>
          {chain.map((l, i) => (
            <button
              key={`${l.task}-${l.attempt}`}
              type="button"
              className={styles.step}
              aria-pressed={i === picked}
              aria-label={`${l.task}${l.attempt > 1 ? ` #${l.attempt}` : ""}, ${l.start}–${l.end}, ${causeLabel(l.cause)}`}
              style={{ flexGrow: l.work + l.lost }}
              onClick={() => setPicked(i)}
            >
              <span className={styles.stepHead}>
                <span className={styles.span}>
                  {l.start}–{l.end}
                </span>
                <span className={styles.cause} data-cause={l.cause}>
                  {causeLabel(l.cause)}
                </span>
              </span>
              <span className={styles.stepTask}>
                {l.task}
                {l.attempt > 1 && ` #${l.attempt}`}
              </span>
            </button>
          ))}
        </div>
      </div>

      {current && (
        <div className={styles.answer} aria-live="polite">
          <span className={styles.answerTitle}>
            {current.task.toUpperCase()}
            {current.attempt > 1 && ` #${current.attempt}`}
          </span>
          <span className={styles.answerText}>{stepText(current, unit)}</span>
        </div>
      )}
    </section>
  );
}
