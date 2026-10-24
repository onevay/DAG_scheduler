import { type ReactNode, useId } from "react";

import type { TaskKind, TaskStatus } from "../api/types";
import styles from "./data.module.css";

type Tone = "surface" | "raised" | "ink" | "accent";

/** Секция экрана с заголовком; `tone` — фон: карточка, белый, тёмный «чертёж» или акцент. */
export function Section({
  title,
  tone = "surface",
  actions,
  className,
  children,
}: {
  title: string;
  tone?: Tone;
  actions?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  const id = useId();
  return (
    <section aria-labelledby={id} className={`${styles.section} ${styles[tone]} ${className ?? ""}`}>
      <div className={styles.sectionHead}>
        <h2 id={id} className={styles.sectionTitle}>
          {title}
        </h2>
        {actions}
      </div>
      {children}
    </section>
  );
}

/** Крупное число с подписью; под ним — шкала или пояснение. */
export function MetricCard({
  label,
  value,
  unit,
  tone = "raised",
  children,
}: {
  label: string;
  value: ReactNode;
  unit?: string;
  tone?: Tone;
  children?: ReactNode;
}) {
  return (
    <div className={`${styles.metric} ${styles[tone]} motion-lift`}>
      <span className={styles.metricLabel}>{label}</span>
      <span className={styles.metricValue}>
        {value}
        {unit && <span className={styles.unit}> {unit}</span>}
      </span>
      {children}
    </div>
  );
}

/** Доля шагами: заполненные — доля, пунктир — остаток. Значение читается из подписи рядом. */
export function PillMeter({
  share,
  steps = 10,
  tone = "ink",
}: {
  share: number;
  steps?: number;
  tone?: "ink" | "accent";
}) {
  const filled = Math.round(Math.min(1, Math.max(0, share)) * steps);
  return (
    <div className={styles.pills} aria-hidden="true">
      {Array.from({ length: steps }, (_, i) => (
        <span key={i} className={`${styles.pill} motion-data`} data-on={i < filled} data-tone={tone} />
      ))}
    </div>
  );
}

/** Статус задачи — всегда цветом и словом; у выполняющейся точка пульсирует. */
export function StatusChip({ status }: { status: TaskStatus }) {
  return (
    <span className={styles.status} data-status={status}>
      <span className={`${styles.dot} ${status === "RUNNING" ? "motion-pulse" : ""}`} aria-hidden="true" />
      {status}
    </span>
  );
}

export function KindSwatch({ kind, children }: { kind: TaskKind; children?: ReactNode }) {
  return (
    <span className={styles.kind} style={{ background: `var(--kind-${kind})` }}>
      {children ?? kind}
    </span>
  );
}

export interface FeedItem {
  key: string;
  time: string;
  type: string;
  text: string;
}

/** Лента записей журнала; новые строки объявляются читалкам экрана. */
export function Feed({ title, items, empty }: { title: string; items: readonly FeedItem[]; empty?: string }) {
  return (
    <div className={styles.feed} aria-live="polite">
      <span className={styles.metricLabel}>{title}</span>
      {items.length === 0 && empty && <span className={styles.empty}>{empty}</span>}
      <ul className={styles.feedList}>
        {items.map((item) => (
          <li key={item.key} className={`${styles.feedRow} motion-feed`}>
            <span className={styles.feedTime}>{item.time}</span>
            <span className={styles.badge} data-type={item.type}>
              {item.type}
            </span>
            <span className={styles.feedText}>{item.text}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
