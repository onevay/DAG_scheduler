import { useState } from "react";

import type { Result } from "../api/types";
import { Icon } from "../shell/Icon";
import { CODES, codeSummary, idleGrid } from "./explain";
import styles from "./Explain.module.css";

/** Простой GPU по узлам и часам с фильтром причин и итог по кодам. */
export function IdleMap({
  idle,
  workers,
  horizon,
  unit,
}: {
  idle: NonNullable<Result["explain"]>["idle"];
  workers: readonly { id: string; gpu: number }[];
  horizon: number;
  unit: string;
}) {
  const [filter, setFilter] = useState<string | null>(null);
  const grid = idleGrid(idle.intervals, workers, horizon);
  const { rows, total, insight } = codeSummary(idle.by_code, unit);

  return (
    <div className={styles.idleRow}>
      <section aria-labelledby="explain-idle" className={styles.idle}>
        <div className={styles.idleHead}>
          <h2 id="explain-idle" className={styles.h2}>
            <span className={styles.icon} aria-hidden="true">
              <Icon name="explain" size={18} />
            </span>
            Почему простаивают GPU
          </h2>
          <div role="group" aria-label="Причина простоя" className={styles.filters}>
            {[null, ...rows.map(([code]) => code)].map((code) => (
              <button
                key={code ?? "all"}
                type="button"
                className={styles.filter}
                aria-pressed={filter === code}
                onClick={() => setFilter(code)}
              >
                <span
                  className={styles.swatch}
                  style={{ background: code ? CODES[code]?.color : "var(--color-ink)" }}
                />
                {code ? (CODES[code]?.label ?? code) : "всё"}
              </button>
            ))}
          </div>
        </div>
        {/* Сетка шире телефона и прокручивается: рамка в фокусе, чтобы прокрутить её с клавиатуры */}
        <div className={styles.grid} tabIndex={0} role="region" aria-label="Простой по узлам и часам">
          <div className={styles.gridScale} aria-hidden="true">
            <span>0 {unit}</span>
            <span>
              {Math.round(horizon / 2)} {unit}
            </span>
            <span>
              {horizon} {unit}
            </span>
          </div>
          {grid.map((row) => (
            <div key={row.worker} className={styles.gridRow}>
              <span className={styles.gridName}>{row.worker}</span>
              <div className={styles.cells} aria-label={`${row.worker}: простой по часам`} role="img">
                {row.cells.map((c, h) => (
                  <span
                    key={h}
                    className={styles.cell}
                    data-part={c.part}
                    data-dim={filter !== null && c.code !== filter}
                    style={{ background: c.code ? CODES[c.code]?.color : "var(--color-ink)" }}
                    title={`${h}–${h + 1} ${unit}: ${c.code ? `${c.code}${c.part ? ", часть GPU" : ""}` : "занят"}`}
                  />
                ))}
              </div>
            </div>
          ))}
        </div>
        <p className={styles.insight}>{insight}</p>
      </section>

      <section aria-labelledby="explain-codes" className={styles.codes}>
        <h2 id="explain-codes" className={styles.caption}>
          Простой, GPU·{unit}
        </h2>
        <span className={styles.metric}>{total}</span>
        {rows.map(([code, value]) => (
          <div key={code} className={styles.code}>
            <span className={styles.codeHead}>
              <code>{code}</code>
              <strong>{value}</strong>
            </span>
            <span className={styles.track}>
              <span
                className="motion-data"
                style={{ width: `${(value / total) * 100}%`, background: CODES[code]?.color }}
              />
            </span>
            <span className={styles.caption}>{CODES[code]?.hint ?? ""}</span>
          </div>
        ))}
      </section>
    </div>
  );
}
