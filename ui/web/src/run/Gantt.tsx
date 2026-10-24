import type { CSSProperties } from "react";

import { ticks } from "../cluster/timeline";
import { phaseAt } from "./journal";
import styles from "./Run.module.css";
import type { GanttBar, JournalOutage } from "./results";

const KINDS = ["data", "train", "eval", "infer"] as const;

export interface Reserve {
  task: string;
  worker: string;
  tRes: number;
  since: number;
}

/**
 * Расписание по дорожкам узлов относительно момента `t`: прошедшие попытки залиты, идущие обведены,
 * будущие — пунктиром; потерянная работа штрихуется, когда момент её прошёл.
 */
export function Gantt({
  bars,
  lanes,
  outages,
  reserves,
  horizon,
  t,
  selected,
  onSelect,
  unit,
}: {
  bars: readonly GanttBar[];
  lanes: readonly string[];
  outages: readonly JournalOutage[];
  reserves: readonly Reserve[];
  horizon: number;
  t: number;
  selected: string | null;
  onSelect: (task: string) => void;
  unit: string;
}) {
  const span = Math.max(horizon, 1);
  const pct = (x: number) => `${(Math.min(Math.max(x, 0), span) / span) * 100}%`;
  return (
    <>
      <div className={styles.legend} aria-hidden="true">
        {KINDS.map((k) => (
          <span key={k}>
            <span className={styles.swatch} style={{ background: `var(--kind-${k})` }} />
            {k}
          </span>
        ))}
        <span>
          <span className={styles.swatch} style={{ background: "var(--hatch-lost)" }} />
          потеряно
        </span>
        <span>
          <span className={styles.swatch} style={{ background: "var(--hatch-down)" }} />
          узел недоступен
        </span>
      </div>
      <div className={styles.scroll}>
        <div className={styles.gantt}>
          {lanes.map((lane) => {
            const worker = lane.slice(0, lane.lastIndexOf(" · "));
            const first = lane.endsWith(" · 1");
            return (
              <div key={lane} className={styles.lane}>
                <span className={styles.laneName}>{lane}</span>
                <div className={styles.track}>
                  {outages
                    .filter((o) => o.worker === worker)
                    .map((o) => (
                      <span
                        key={o.from}
                        className={styles.down}
                        style={{ left: pct(o.from), width: pct(o.to - o.from) }}
                      >
                        {first && `недоступен ${o.from}–${o.to}`}
                      </span>
                    ))}
                  {first &&
                    reserves
                      .filter((r) => r.worker === worker && r.since <= t && t < r.tRes)
                      .map((r) => (
                        <span
                          key={r.task}
                          className={styles.reserve}
                          style={{ left: pct(r.tRes) }}
                          title={`резерв ${r.task} к ${r.tRes} ${unit}`}
                        />
                      ))}
                  {bars
                    .filter((b) => b.lane === lane)
                    .map((b) => {
                      const phase = phaseAt(b, t);
                      const lost =
                        b.lostFrom !== null && t > b.lostFrom
                          ? ((b.end - b.lostFrom) / Math.max(b.end - b.start, 1)) * 100
                          : 0;
                      return (
                        <button
                          key={`${b.task}-${b.attempt}`}
                          type="button"
                          className={styles.bar}
                          data-phase={phase}
                          data-submitted={b.submitted}
                          data-late={b.late}
                          aria-pressed={b.task === selected}
                          aria-label={`${b.task}, попытка ${b.attempt}, ${b.start}–${b.end} ${unit}${b.outcome === "INT" ? ", прервана" : b.outcome === "PRE" ? ", вытеснена" : ""}`}
                          style={
                            {
                              left: pct(b.start),
                              width: `calc(${pct(b.end - b.start)} - 3px)`,
                              "--kind": `var(--kind-${b.kind})`,
                            } as CSSProperties
                          }
                          onClick={() => onSelect(b.task)}
                        >
                          <span className={styles.barText}>{b.task}</span>
                          <span className={`${styles.lost} motion-data`} style={{ width: `${lost}%` }} />
                        </button>
                      );
                    })}
                  <span className={styles.cursor} style={{ left: pct(t) }} aria-hidden="true" />
                </div>
              </div>
            );
          })}
          <div className={styles.lane} aria-hidden="true">
            <span className={styles.laneName} />
            <div className={styles.ticks}>
              {ticks(span).map((x) => (
                // Крайнее правое деление прижато к концу шкалы, иначе подпись обрезается
                <span
                  key={x}
                  style={{ left: pct(x), transform: x === span ? "translateX(-100%)" : undefined }}
                >
                  {x}
                </span>
              ))}
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
