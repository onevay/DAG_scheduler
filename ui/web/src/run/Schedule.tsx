import { useMemo, useState } from "react";

import { useRunEvents } from "../api/hooks";
import type { Problem, Result } from "../api/types";
import { Gantt } from "./Gantt";
import { MomentInfo, Scrubber } from "./Moment";
import { ganttBars, journalOutages, laneOrder, reservations } from "./results";
import styles from "./Run.module.css";
import { TaskAside } from "./TaskAside";

/** Расписание прогона во времени: Гант, момент, журнал к моменту и выбранная задача. */
export function Schedule({
  runId,
  result,
  problem,
  unit,
}: {
  runId: string;
  result: Result;
  problem: Problem;
  unit: string;
}) {
  const journal = useRunEvents(runId);
  const events = useMemo(() => journal.data ?? [], [journal.data]);
  const bars = useMemo(() => ganttBars(result, problem), [result, problem]);
  const lanes = useMemo(
    () =>
      laneOrder(
        bars,
        problem.cluster.workers.map((w) => w.id),
      ),
    [bars, problem],
  );
  const horizon = result.metrics?.makespan || Math.max(1, ...bars.map((b) => b.end));
  const outages = useMemo(() => journalOutages(events, horizon), [events, horizon]);
  const reserves = useMemo(() => reservations(events), [events]);
  // Начинаем с конца прогона: сразу видно всё расписание; ползунок отматывает назад
  const [t, setT] = useState(horizon);
  const [selected, setSelected] = useState<string | null>(null);

  return (
    <div className={styles.layout}>
      <section aria-labelledby="run-schedule" className={styles.schedule}>
        <div className={styles.head}>
          <h2 id="run-schedule" className={styles.h2}>
            Расписание
          </h2>
        </div>
        <Gantt
          bars={bars}
          lanes={lanes}
          outages={outages}
          reserves={reserves}
          horizon={horizon}
          t={t}
          selected={selected}
          onSelect={setSelected}
          unit={unit}
        />
        <Scrubber t={t} onChange={setT} horizon={horizon} events={events} unit={unit} />
        {journal.isError && <p className={styles.muted}>Журнал не загрузился: {journal.error.message}</p>}
        <MomentInfo t={t} bars={bars} events={events} unit={unit} />
      </section>
      <TaskAside runId={runId} result={result} task={selected} unit={unit} />
    </div>
  );
}
