import type { JournalRecord, Shadow } from "../api/types";
import { Section } from "../ui/data";
import { delta } from "./PlanVsFact";
import styles from "./Containers.module.css";
import { failureLine, failures, KINDS, type Kind, summary } from "./failureFeed";

/** Сбои прогона: сводка (по видам, восстановлено, сохранено и потеряно работы, makespan против плана) и
 * лента «убита → повтор с позиции S». Без сбоев ничего не показывает. */
export function Failures({
  events,
  unit,
  done,
  shadow,
}: {
  events: readonly JournalRecord[];
  unit: string;
  done: boolean;
  shadow?: Shadow | null;
}) {
  const list = failures(events);
  if (list.length === 0) return null;
  const s = summary(events, done, list);
  const kinds = (Object.keys(KINDS) as Kind[]).filter((k) => s.byKind[k]);
  return (
    <Section title={`Сбои · ${s.total}`}>
      <ul className={styles.causes} aria-label="Сбои по видам">
        {kinds.map((k) => (
          <li key={k} className={styles.cause}>
            {KINDS[k]} · {s.byKind[k]}
          </li>
        ))}
      </ul>
      <p className={styles.note} aria-label="Итог сбоев">
        Задач со сбоями восстановлено: {s.recovered}
        {s.failed > 0 && `, не выполнено: ${s.failed}`}. Работы сохранено чекпоинтами {s.saved} {unit},
        потеряно {s.lost} {unit}
        {shadow &&
          `. Makespan ${shadow.fact_makespan} ${unit} против ${shadow.plan_makespan} ${unit} по плану (${delta(shadow.fact_makespan - shadow.plan_makespan)})`}
        .
      </p>
      <ol className={styles.log} aria-label="Лента сбоев">
        {list.map((f) => (
          <li key={`${f.task}#${f.attempt}`}>{failureLine(f, unit, done)}</li>
        ))}
      </ol>
    </Section>
  );
}
