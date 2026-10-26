import type { Shadow } from "../api/types";
import table from "../pages/RunPage.module.css";
import { Section } from "../ui/data";
import styles from "./Containers.module.css";

type Cause = Shadow["tasks"][string]["cause"];

export const CAUSES: Record<Cause, string> = {
  same: "по плану",
  waiting: "ещё не закончена",
  manual: "ручной отказ узла",
  failure: "сбой попытки",
  duration: "попытка шла не по плану",
  upstream: "зависимость позже плана",
  decision: "другое решение политики",
};

const span = (start: number | null, finish: number | null) =>
  start == null ? "—" : `${start}–${finish ?? "…"}`;
export const delta = (d: number | null) => (d == null ? "—" : d > 0 ? `+${d}` : d < 0 ? `−${-d}` : "0");

/** План (симуляция того же входа) рядом с фактом прогона на агентах и причина расхождения по задаче. */
export function PlanVsFact({ shadow, unit }: { shadow: Shadow; unit: string }) {
  const rows = Object.entries(shadow.tasks).sort(
    ([a, x], [b, y]) => (x.plan_start ?? Infinity) - (y.plan_start ?? Infinity) || a.localeCompare(b),
  );
  const causes = (Object.keys(CAUSES) as Cause[]).filter((c) => shadow.causes[c]);
  return (
    <Section title="План и факт">
      <p className={styles.note}>
        Makespan: план {shadow.plan_makespan} {unit} · факт {shadow.fact_makespan} {unit} (
        {delta(shadow.fact_makespan - shadow.plan_makespan)})
      </p>
      <ul className={styles.causes} aria-label="Причины расхождений">
        {causes.map((c) => (
          <li key={c} className={styles.cause} data-same={c === "same"}>
            {CAUSES[c]} · {shadow.causes[c]}
          </li>
        ))}
      </ul>
      <div className={table.scroll} tabIndex={0} role="region" aria-label="План и факт по задачам">
        <table className={table.table}>
          <thead>
            <tr>
              <th scope="col">Задача</th>
              <th scope="col">План, {unit}</th>
              <th scope="col">Факт, {unit}</th>
              <th scope="col">Сдвиг финиша</th>
              <th scope="col">Причина</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(([task, t]) => (
              <tr key={task}>
                <th scope="row">
                  <code>{task}</code>
                </th>
                <td>{span(t.plan_start, t.plan_finish)}</td>
                <td>{span(t.fact_start, t.fact_finish)}</td>
                <td>{delta(t.finish_delta)}</td>
                <td>{CAUSES[t.cause]}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Section>
  );
}
