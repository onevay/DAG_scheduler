import { useEffect, useRef, useState } from "react";

import type { JournalRecord } from "../api/types";
import { Feed } from "../ui/data";
import { feedAt, marksOf, phaseAt } from "./journal";
import styles from "./Run.module.css";
import type { GanttBar } from "./results";

// Проигрывание всего прогона занимает ~10 с при любой его длине
const TICK_MS = 120;
const STEPS = 88;

// Момент — с точностью до десятой: шаги проигрывания дробные, а 4.999… читать незачем
const format = (t: number) => String(Math.round(t * 10) / 10);

/** Момент прогона: ползунок, проигрывание, переходы к событиям. */
export function Scrubber({
  t,
  onChange,
  horizon,
  events,
  unit,
}: {
  t: number;
  onChange: (t: number) => void;
  horizon: number;
  events: readonly JournalRecord[];
  unit: string;
}) {
  const [playing, setPlaying] = useState(false);
  const start = useRef(t);
  start.current = t;
  // Интервал заводится один раз на проигрывание и сам ведёт момент: не зависит от того, успел ли
  // перерисоваться родитель между шагами
  useEffect(() => {
    if (!playing) return;
    let current = start.current;
    const timer = setInterval(() => {
      current = Math.min(horizon, current + horizon / STEPS);
      onChange(current);
      if (current >= horizon) setPlaying(false);
    }, TICK_MS);
    return () => clearInterval(timer);
  }, [playing, horizon, onChange]);

  const toggle = () => {
    if (!playing && t >= horizon) {
      start.current = 0;
      onChange(0);
    }
    setPlaying(!playing);
  };
  return (
    <div className={styles.scrubber}>
      <button
        type="button"
        className={styles.play}
        aria-label={playing ? "Пауза" : "Проиграть"}
        onClick={toggle}
      >
        <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">
          {playing ? (
            <path d="M8 5v14M16 5v14" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
          ) : (
            <path d="M8 5v14l11-7z" fill="currentColor" />
          )}
        </svg>
      </button>
      <label htmlFor="run-moment" className={styles.moment}>
        T = {format(t)} {unit.toUpperCase()}
      </label>
      <input
        id="run-moment"
        type="range"
        min={0}
        max={horizon}
        step={0.5}
        value={t}
        onChange={(e) => {
          setPlaying(false);
          onChange(Number(e.target.value));
        }}
      />
      <div className={styles.marks}>
        {marksOf(events).map((m) => (
          <button
            key={m.label}
            type="button"
            className={styles.markButton}
            onClick={() => {
              setPlaying(false);
              onChange(m.t);
            }}
          >
            {m.label}
          </button>
        ))}
      </div>
    </div>
  );
}

/** Что идёт в момент `t` и что журнал успел записать к нему. */
export function MomentInfo({
  t,
  bars,
  events,
  unit,
}: {
  t: number;
  bars: readonly GanttBar[];
  events: readonly JournalRecord[];
  unit: string;
}) {
  const running = bars.filter((b) => phaseAt(b, t) === "now");
  return (
    <div className={styles.momentRow}>
      <div className={styles.card}>
        <span className={styles.cardLabel}>
          Идут в момент {format(t)} {unit}
        </span>
        <div className={styles.running}>
          {running.length === 0 && <span className={styles.muted}>ничего не выполняется</span>}
          {running.map((b) => (
            <span
              key={`${b.task}-${b.attempt}`}
              className={styles.runningChip}
              style={{ background: `var(--kind-${b.kind})` }}
            >
              {b.task} · {b.worker}
            </span>
          ))}
        </div>
      </div>
      <Feed title="Журнал до этого момента" items={feedAt(events, t, unit)} empty="записей пока нет" />
    </div>
  );
}
