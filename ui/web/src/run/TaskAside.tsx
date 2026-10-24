import { useExplain } from "../api/hooks";
import type { Result } from "../api/types";
import { StatusChip } from "../ui/data";
import { replanRows } from "./results";
import styles from "./Run.module.css";

const OUTCOMES = { DONE: "готово", INT: "прервана", PRE: "вытеснена" } as const;
const TRIGGERS: Record<string, string> = {
  worker_down: "отказ",
  worker_up: "возврат",
  submit_task: "подача",
};

/** Подробности выбранной задачи: попытки, почему ждала (из /explain), перепланирования прогона. */
export function TaskAside({
  runId,
  result,
  task,
  unit,
}: {
  runId: string;
  result: Result;
  task: string | null;
  unit: string;
}) {
  const summary = task ? result.tasks[task] : undefined;
  const explain = useExplain(runId, { task: task ?? undefined }, task !== null);
  const tries = result.attempts.filter((a) => a.task === task);
  // Строки перепланирований — как в Streamlit, только причины по-русски
  const replans = replanRows(result.replans).map((r, i) => ({
    ...r,
    events: (result.replans?.[i]?.events ?? [])
      .map((e) => `${TRIGGERS[e.type] ?? e.type} ${e.task ?? e.worker ?? ""}`)
      .join(", "),
  }));
  const U = unit.toUpperCase();

  return (
    <aside aria-label="Задача" className={styles.aside}>
      {!task || !summary ? (
        <p className={styles.asideMuted}>Нажмите на полосу задачи, чтобы увидеть её попытки и ожидания.</p>
      ) : (
        <>
          <div className={styles.asideHead}>
            <span className={styles.asideTitle}>
              <span className={styles.asideMuted}>Выбранная задача</span>
              <span className={styles.asideId}>{task}</span>
            </span>
            <StatusChip status={summary.status} />
          </div>
          <div className={styles.facts}>
            {[
              ["Старт", summary.start != null ? `${summary.start} ${U}` : "—"],
              ["Конец", summary.finish != null ? `${summary.finish} ${U}` : "—"],
              ["Попыток", tries.length],
            ].map(([label, value]) => (
              <span key={label} className={styles.fact}>
                <span className={styles.asideMuted}>{label}</span>
                <span className={styles.factValue}>{value}</span>
              </span>
            ))}
          </div>
          <div className={styles.asideBlock}>
            <span className={styles.asideMuted}>Попытки</span>
            {tries.map((a) => (
              <div key={a.attempt} className={styles.tile}>
                <code>
                  #{a.attempt} {a.worker}
                </code>
                <span>
                  {a.start}–{a.end ?? "…"} {unit}
                </span>
                <span className={styles.outcome} data-outcome={a.outcome ?? "none"}>
                  {a.outcome ? OUTCOMES[a.outcome] : "не завершена"}
                </span>
              </div>
            ))}
          </div>
          <div className={styles.asideBlock} aria-busy={explain.isFetching}>
            <span className={styles.asideMuted}>Почему ждала</span>
            {explain.isError && (
              <span className={styles.asideMuted}>Не загрузилось: {explain.error.message}</span>
            )}
            {explain.data?.waits.length === 0 && (
              <span className={styles.asideMuted}>Не ждала: стартовала сразу.</span>
            )}
            {explain.data?.waits.map((w) => (
              <div key={`${w.code}-${w.from}`} className={`${styles.tile} ${styles.wait}`}>
                <span className={styles.waitHead}>
                  <code className={styles.code}>{w.code}</code>
                  <span>
                    {w.from}–{w.to} {unit}
                  </span>
                </span>
                {Object.keys(w.detail).length > 0 && (
                  <span className={styles.asideMuted}>
                    {Object.entries(w.detail)
                      .map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(", ") : String(v)}`)
                      .join(" · ")}
                  </span>
                )}
              </div>
            ))}
          </div>
        </>
      )}
      {replans.length > 0 && (
        <div className={styles.asideBlock}>
          <span className={styles.asideMuted}>Перепланирования</span>
          {replans.map((r) => (
            <span key={r.t} className={styles.replan}>
              {r.t} {unit} · {r.events}: {r.shifted || "сдвигов нет"}
              {r.added && `; новые: ${r.added}`}
              {r.dropped && `; сняты: ${r.dropped}`}
            </span>
          ))}
        </div>
      )}
    </aside>
  );
}
