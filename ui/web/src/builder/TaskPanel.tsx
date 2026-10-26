import { type ReactNode, useEffect, useId, useState } from "react";

import type { Task, TaskKind } from "../api/types";
import { useDraft } from "../draft/DraftContext";
import { Chip, NumberField, Stepper, Switch } from "../ui/controls";
import styles from "./TaskPanel.module.css";
import { ResourceFields } from "../ui/ResourceFields";
import { fits } from "../ui/resources";

const KINDS: readonly TaskKind[] = ["data", "train", "eval", "infer", "other"];
const UNITS = { hour: "ч", minute: "мин", second: "с" } as const;

/** Класс приоритета — подпись к числу, как в макете: от фоновых задач до срочных. */
export function priorityClass(priority: number): string {
  return priority >= 8 ? "critical" : priority >= 5 ? "high" : priority >= 2 ? "normal" : "low";
}

/** `gpu=a100, zone=eu` ↔ `{gpu: "a100", zone: "eu"}`; пары без «=» отбрасываются. */
export function parseConstraints(text: string): Record<string, string> {
  return Object.fromEntries(
    text
      .split(",")
      .map((pair) => pair.split("=").map((s) => s.trim()))
      .filter((kv): kv is [string, string] => kv.length === 2 && kv[0] !== "" && kv[1] !== ""),
  );
}

const formatConstraints = (c: Record<string, string> | undefined) =>
  Object.entries(c ?? {})
    .map(([k, v]) => `${k}=${v}`)
    .join(", ");

export function TaskPanel({
  selected,
  onSelect,
}: {
  selected: string | null;
  onSelect: (id: string | null) => void;
}) {
  const { draft, dispatch } = useDraft();
  const eventIndex = (draft.events ?? []).findIndex(
    (e) => e.type === "submit_task" && e.task?.id === selected,
  );
  const event = draft.events?.[eventIndex];
  const task = draft.tasks.find((t) => t.id === selected) ?? event?.task ?? undefined;
  if (!task) {
    return (
      <aside aria-label="Свойства задачи" className={styles.panel}>
        <p className={styles.hint}>Выберите задачу на холсте или добавьте новую из палитры.</p>
      </aside>
    );
  }

  const unit = UNITS[draft.time_unit ?? "hour"];
  // Поданная задача живёт в событии submit_task: правки идут туда же
  const update = (patch: Partial<Task>) =>
    event
      ? dispatch({ type: "updateEvent", index: eventIndex, patch: { task: { ...task, ...patch } } })
      : dispatch({ type: "updateTask", id: task.id, patch });
  const taken = new Set([
    ...draft.tasks.map((t) => t.id),
    ...(draft.events ?? []).flatMap((e) => e.task?.id ?? []),
  ]);
  const rename = (id: string) => {
    if (!id || id === task.id || taken.has(id)) return;
    update({ id });
    onSelect(id);
  };
  const placed = draft.cluster.workers.filter((w) => fits(task.resources, w.resources)).length;
  const priority = task.priority ?? 1;
  const deps = task.dependencies ?? [];
  const candidates = draft.tasks.filter((t) => t.id !== task.id && !deps.includes(t.id));

  return (
    <aside aria-label="Свойства задачи" className={styles.panel}>
      <div className={styles.head}>
        <TextField label="Задача" value={task.id} onCommit={rename} mono />
        <span className={styles.kindBadge} style={{ background: `var(--kind-${task.kind ?? "other"})` }}>
          {task.kind ?? "other"}
        </span>
      </div>

      <Field label="Тип">
        <div className={styles.chips}>
          {KINDS.map((k) => (
            <Chip
              key={k}
              pressed={(task.kind ?? "other") === k}
              onToggle={() => update({ kind: k })}
              color={`var(--kind-${k})`}
            >
              {k}
            </Chip>
          ))}
        </div>
      </Field>

      <div className={styles.pair}>
        <Stepper
          label="Длительность"
          unit={unit}
          value={task.duration}
          min={1}
          onChange={(duration) => update({ duration })}
          less="Короче"
          more="Дольше"
        />
      </div>
      <ResourceFields value={task.resources} onChange={(resources) => update({ resources })} of="задачи" />
      <p className={styles.hint}>
        {placed > 0
          ? `помещается на ${placed} из ${draft.cluster.workers.length} узлов`
          : "не помещается ни на один узел"}
      </p>

      <div className={styles.pair}>
        <Stepper
          label={`Приоритет · ${priorityClass(priority)}`}
          value={priority}
          min={0}
          max={10}
          onChange={(p) => update({ priority: p })}
          less="Ниже приоритет"
          more="Выше приоритет"
        />
        <Stepper
          label="Ретраи"
          value={task.max_retries ?? 2}
          min={0}
          max={20}
          onChange={(n) => update({ max_retries: n })}
          less="Меньше ретраев"
          more="Больше ретраев"
        />
      </div>

      <Field
        label={`Чекпоинт · ${task.checkpoint_interval ? `каждые ${task.checkpoint_interval} ${unit}` : "нет"}`}
      >
        <input
          type="range"
          min={0}
          max={Math.max(1, task.duration)}
          value={task.checkpoint_interval ?? 0}
          onChange={(e) => update({ checkpoint_interval: Number(e.target.value) || null })}
          aria-label="Интервал чекпоинта"
        />
        <span className={styles.hint}>При отказе узла теряется работа после последнего чекпоинта</span>
      </Field>

      <Switch
        label="Можно вытеснить"
        hint="ради срочной задачи класса выше"
        checked={task.preemptible ?? false}
        onChange={(preemptible) => update({ preemptible })}
      />

      <div className={styles.pair}>
        <NumberField
          label={`Дедлайн, ${unit}`}
          value={task.deadline}
          onChange={(deadline) => update({ deadline })}
          placeholder="нет"
        />
        <NumberField
          label={`Готова с, ${unit}`}
          value={task.release_time}
          onChange={(t) => update({ release_time: t ?? 0 })}
          placeholder="0"
        />
      </div>
      {event && (
        <NumberField
          label={`Подаётся в момент, ${unit}`}
          value={event.time}
          onChange={(time) =>
            dispatch({ type: "updateEvent", index: eventIndex, patch: { time: time ?? 0 } })
          }
        />
      )}
      <TextField
        label="Ограничения по меткам узлов"
        value={formatConstraints(task.constraints)}
        placeholder="gpu=a100, zone=eu"
        onCommit={(text) => update({ constraints: parseConstraints(text) })}
      />

      <Field label="Зависит от">
        <div className={styles.chips}>
          {deps.length === 0 && <span className={styles.hint}>ни от чего: стартует сразу</span>}
          {deps.map((dep) => (
            <button
              key={dep}
              type="button"
              className={styles.dep}
              aria-label={`Убрать зависимость от ${dep}`}
              onClick={() => update({ dependencies: deps.filter((d) => d !== dep) })}
            >
              {dep} ×
            </button>
          ))}
        </div>
        {candidates.length > 0 && (
          <select
            className={styles.input}
            aria-label="Добавить зависимость"
            value=""
            onChange={(e) => e.target.value && update({ dependencies: [...deps, e.target.value] })}
          >
            <option value="">добавить зависимость…</option>
            {candidates.map((t) => (
              <option key={t.id} value={t.id}>
                {t.id}
              </option>
            ))}
          </select>
        )}
      </Field>
    </aside>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div role="group" aria-label={label} className={styles.field}>
      <span className={styles.label}>{label}</span>
      {children}
    </div>
  );
}

/** Текст, который применяется по Enter или при уходе с поля: так id и ограничения не меняются на каждой букве. */
function TextField({
  label,
  value,
  onCommit,
  placeholder,
  mono,
}: {
  label: string;
  value: string;
  onCommit: (value: string) => void;
  placeholder?: string;
  mono?: boolean;
}) {
  const id = useId();
  const [text, setText] = useState(value);
  useEffect(() => setText(value), [value]);
  // Отклонённое значение (занятый id) возвращается к прежнему
  const commit = () => {
    if (text.trim() !== value) onCommit(text.trim());
    setText(value);
  };
  return (
    <div className={styles.field}>
      <label htmlFor={id} className={styles.label}>
        {label}
      </label>
      <input
        id={id}
        className={`${styles.input} ${mono ? styles.mono : ""}`}
        value={text}
        placeholder={placeholder}
        onChange={(e) => setText(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => e.key === "Enter" && commit()}
      />
    </div>
  );
}
