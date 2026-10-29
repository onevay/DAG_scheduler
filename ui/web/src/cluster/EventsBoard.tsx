import { useEffect, useState } from "react";

import type { InputEvent } from "../api/types";
import { freeId } from "../draft/draft";
import { useDraft } from "../draft/DraftContext";
import styles from "./Cluster.module.css";
import { type Outage, ticks } from "./timeline";
import { clusterResources, mainResource, work } from "../ui/resources";

const TYPES: Record<InputEvent["type"], string> = {
  worker_down: "отказ",
  worker_up: "возврат",
  submit_task: "подача",
};

function describe(e: InputEvent, unit: string): string {
  if (e.type === "submit_task") {
    const t = e.task;
    if (!t) return "задача не задана";
    return `${t.id} — priority ${t.priority ?? 1}${t.deadline != null ? `, дедлайн ${t.deadline} ${unit}` : ""}`;
  }
  return e.type === "worker_down"
    ? `${e.worker} — задачи прерываются, работа после чекпоинта теряется`
    : `${e.worker} — возвращается пустым`;
}

/** Шкала и список событий входа: отказы и возвраты узлов, подача задач во время прогона. */
export function EventsBoard({
  down,
  horizon,
  unit,
}: {
  down: readonly Outage[];
  horizon: number;
  unit: string;
}) {
  const { draft, dispatch } = useDraft();
  const events = draft.events ?? [];
  const workers = draft.cluster.workers;
  const pct = (t: number) => `${(Math.min(t, horizon) / horizon) * 100}%`;
  const downHours = down.reduce((s, o) => s + o.to - o.from, 0);
  const main = mainResource(clusterResources(workers));
  const capacityOf = new Map(workers.map((w) => [w.id, main ? (w.resources[main] ?? 0) : 0]));
  const lost = down.reduce((s, o) => s + (o.to - o.from) * (capacityOf.get(o.worker) ?? 0), 0);
  const rows = [
    ...workers.map((w) => ({
      name: w.id,
      spans: down.filter((o) => o.worker === w.id),
      marks: events.filter((e) => e.worker === w.id),
    })),
    { name: "подача задач", spans: [], marks: events.filter((e) => e.type === "submit_task") },
  ];

  return (
    <section aria-label="События кластера" className={styles.board}>
      <div className={styles.boardHead}>
        <div className={styles.stack}>
          <span className={styles.tech}>СОБЫТИЯ · {events.length}</span>
          <span className={styles.caption}>Отказы и возвраты узлов, подача задач во время прогона</span>
        </div>
        <div className={styles.totals}>
          <span className={styles.stack}>
            <span className={styles.techSm}>
              {downHours} {unit.toUpperCase()}
            </span>
            <span className={styles.caption}>узлы недоступны</span>
          </span>
          {main && (
            <span className={styles.stack}>
              <span className={styles.techSm}>{work(main, lost, unit.toUpperCase())}</span>
              <span className={styles.caption}>выпало из ёмкости</span>
            </span>
          )}
        </div>
      </div>

      <div className={styles.scroll} aria-hidden="true">
        <div className={styles.timeline}>
          {rows.map((row) => (
            <div key={row.name} className={styles.row}>
              <span className={styles.rowName}>{row.name}</span>
              <div className={styles.track}>
                {row.spans.map((o) => (
                  <span
                    key={o.from}
                    className={styles.span}
                    style={{ left: pct(o.from), width: pct(o.to - o.from) }}
                  />
                ))}
                {row.marks.map((e, i) => (
                  <span
                    key={i}
                    className={styles.mark}
                    data-type={e.type}
                    style={{ left: pct(e.time) }}
                    title={`${TYPES[e.type]} в ${e.time} ${unit}`}
                  />
                ))}
              </div>
            </div>
          ))}
          <div className={styles.row}>
            <span className={styles.rowName} />
            <div className={styles.ticks}>
              {ticks(horizon).map((t) => (
                <span key={t} style={{ left: pct(t) }}>
                  {t}
                </span>
              ))}
            </div>
          </div>
        </div>
      </div>

      <ul className={styles.events}>
        {events.map((e, index) => (
          <li key={index} className={styles.event}>
            <TimeInput
              value={e.time}
              unit={unit}
              label={`Момент события ${TYPES[e.type]}, ${unit}`}
              onCommit={(time) => dispatch({ type: "updateEvent", index, patch: { time } })}
            />
            <span className={styles.badge} data-type={e.type}>
              {TYPES[e.type]}
            </span>
            <span className={styles.eventText}>{describe(e, unit)}</span>
            <button
              type="button"
              className={styles.remove}
              aria-label={`Удалить событие ${TYPES[e.type]} в ${e.time} ${unit}`}
              onClick={() => dispatch({ type: "removeEvent", index })}
            >
              ×
            </button>
          </li>
        ))}
      </ul>
      <AddEvent unit={unit} />
    </section>
  );
}

/** Момент события; применяется по Enter или уходу с поля — список пересортируется не на каждой цифре. */
function TimeInput({
  value,
  unit,
  label,
  onCommit,
}: {
  value: number;
  unit: string;
  label: string;
  onCommit: (time: number) => void;
}) {
  const [text, setText] = useState(String(value));
  useEffect(() => setText(String(value)), [value]);
  const commit = () => {
    const time = Math.max(0, Math.round(Number(text)));
    if (Number.isFinite(time) && time !== value) onCommit(time);
    else setText(String(value));
  };
  return (
    <label className={styles.time}>
      <input
        type="number"
        min={0}
        value={text}
        aria-label={label}
        onChange={(e) => setText(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => e.key === "Enter" && commit()}
      />
      {unit}
    </label>
  );
}

/** Новое событие: отказ или возврат выбранного узла либо подача новой срочной задачи. */
function AddEvent({ unit }: { unit: string }) {
  const { draft, dispatch } = useDraft();
  const [type, setType] = useState<InputEvent["type"]>("worker_down");
  const [worker, setWorker] = useState("");
  const [time, setTime] = useState(0);
  const workers = draft.cluster.workers;
  const target = worker || workers[0]?.id;
  const add = () => {
    if (type === "submit_task") {
      const taken = [
        ...draft.tasks.map((t) => t.id),
        ...(draft.events ?? []).flatMap((e) => e.task?.id ?? []),
      ];
      const main = mainResource(clusterResources(workers));
      const resources = main ? { [main]: main === "ram_mb" ? 1024 : 1 } : {};
      const task = { id: freeId("urgent", taken), duration: 1, resources, priority: 10 };
      dispatch({ type: "addEvent", event: { time, type, task } });
    } else if (target) {
      dispatch({ type: "addEvent", event: { time, type, worker: target } });
    }
  };
  return (
    <div role="group" aria-label="Новое событие" className={styles.addEvent}>
      <select
        aria-label="Тип события"
        value={type}
        onChange={(e) => setType(e.target.value as InputEvent["type"])}
      >
        {Object.entries(TYPES).map(([value, label]) => (
          <option key={value} value={value}>
            {label}
          </option>
        ))}
      </select>
      {type !== "submit_task" && (
        <select aria-label="Узел" value={target ?? ""} onChange={(e) => setWorker(e.target.value)}>
          {workers.map((w) => (
            <option key={w.id} value={w.id}>
              {w.id}
            </option>
          ))}
        </select>
      )}
      <label className={styles.time}>
        <input
          type="number"
          min={0}
          value={time}
          aria-label={`Момент нового события, ${unit}`}
          onChange={(e) => setTime(Math.max(0, Math.round(Number(e.target.value))))}
        />
        {unit}
      </label>
      <button
        type="button"
        className={styles.soft}
        onClick={add}
        disabled={type !== "submit_task" && !target}
      >
        Добавить
      </button>
      <span className={styles.caption}>Подача создаёт срочную задачу — её свойства правятся в «Сборке».</span>
    </div>
  );
}
