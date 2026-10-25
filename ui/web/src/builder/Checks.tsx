import type { UseQueryResult } from "@tanstack/react-query";

import { usePolicies } from "../api/hooks";
import type { Policy, Validation } from "../api/types";
import { diagnosticsByTask } from "../draft/draft";
import { useDraft } from "../draft/DraftContext";
import { NumberField, type Option, Segmented, Stepper } from "../ui/controls";
import { Section } from "../ui/data";
import styles from "./Checks.module.css";
import { EDGES, plural, TASKS } from "../ui/plural";

/** Итог проверки входа: статус, объём работы и диагностики по задачам; id задачи выбирает её на холсте. */
export function CheckStrip({
  validation,
  onSelect,
}: {
  validation: UseQueryResult<Validation>;
  onSelect: (id: string) => void;
}) {
  const { draft } = useDraft();
  if (draft.tasks.length === 0) return null;

  const edges = draft.tasks.reduce((n, t) => n + (t.dependencies?.length ?? 0), 0);
  const work = draft.tasks.reduce((n, t) => n + t.duration * (t.resources?.gpu ?? 0), 0);
  const data = validation.data;
  const errors = data?.diagnostics.filter((d) => d.level === "error") ?? [];
  const warnings = (data?.diagnostics.length ?? 0) - errors.length;
  const title = validation.isError
    ? "Проверка не удалась"
    : !data
      ? "Проверка…"
      : data.valid
        ? "Вход корректен"
        : `Ошибок: ${errors.length}`;
  const detail = validation.isError
    ? validation.error.message
    : data?.valid
      ? `Нижняя граница makespan — ${data.analysis?.lower_bound ?? "?"}${warnings ? `, предупреждений: ${warnings}` : ""}`
      : errors[0]?.hint;

  return (
    <section
      aria-label="Проверка входа"
      aria-live="polite"
      aria-busy={validation.isFetching}
      className={styles.strip}
      data-ok={data?.valid ?? false}
    >
      <div className={styles.summary}>
        <span className={styles.icon} aria-hidden="true">
          {data?.valid ? "✓" : "!"}
        </span>
        <span className={styles.text}>
          <span className={styles.title}>{title}</span>
          {detail && <span className={styles.detail}>{detail}</span>}
        </span>
        <span className={styles.counts}>
          <span>{plural(draft.tasks.length, TASKS)}</span>
          <span>{plural(edges, EDGES)}</span>
          <span>
            {work} GPU·{draft.time_unit === "minute" ? "мин" : draft.time_unit === "second" ? "с" : "ч"}{" "}
            работы
          </span>
        </span>
      </div>
      {data && data.diagnostics.length > 0 && (
        <ul className={styles.groups}>
          {[...diagnosticsByTask(draft, data.diagnostics)].map(([task, items]) => (
            <li key={task}>
              {task ? (
                <button type="button" className={styles.task} onClick={() => onSelect(task)}>
                  {task}
                </button>
              ) : (
                <span className={styles.task}>весь вход</span>
              )}
              <ul>
                {items.map((d, i) => (
                  <li key={i} data-level={d.level}>
                    <code>{d.code}</code> {d.hint}
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

const POLICY_LABELS: Record<string, string> = {
  fifo: "FIFO",
  fifo_skip: "FIFO с пропуском",
  priority: "По приоритету",
  priority_backfill: "Приоритет + backfill",
  cpsat: "CP-SAT",
};

const VALIDATION: readonly Option<NonNullable<Policy["validation_mode"]>>[] = [
  { value: "lenient", label: "мягкая" },
  { value: "strict", label: "строгая" },
];

const UNITS = { hour: "ч", minute: "мин", second: "с" } as const;

const PREEMPTION: readonly Option<NonNullable<Policy["preemption"]>>[] = [
  { value: "never", label: "никогда" },
  { value: "deadline_only", label: "ради дедлайна" },
  { value: "priority", label: "ради приоритета" },
];

/** Политика планирования черновика — все параметры; незаданные поля берутся из умолчаний API. */
export function PolicySettings() {
  const { draft, dispatch } = useDraft();
  const policies = usePolicies();
  const defaults = policies.data?.defaults as Partial<Policy> | undefined;
  const policy = { ...defaults, ...draft.policy };
  const set = (patch: Partial<Policy>) => dispatch({ type: "setPolicy", patch });
  const unit = UNITS[draft.time_unit ?? "hour"];
  const names = (policies.data?.policies ?? []).map((name) => ({
    value: name,
    label: POLICY_LABELS[name] ?? name,
  }));

  return (
    <Section title="Политика">
      {names.length > 0 && policy.name && (
        <Segmented
          label="Политика планирования"
          options={names}
          value={policy.name}
          onChange={(name) => set({ name: name as Policy["name"] })}
        />
      )}
      {policies.isError && (
        <p className={styles.detail}>Список политик недоступен: {policies.error.message}</p>
      )}
      <div className={styles.row}>
        <div className={styles.labelled}>
          <span className={styles.detail}>Вытеснение</span>
          <Segmented
            label="Вытеснение"
            options={PREEMPTION}
            value={policy.preemption ?? "deadline_only"}
            onChange={(preemption) => set({ preemption })}
          />
        </div>
        <Stepper
          label="Вытеснений на задачу"
          value={policy.max_preemptions_per_task ?? 1}
          min={0}
          max={10}
          onChange={(n) => set({ max_preemptions_per_task: n })}
        />
      </div>
      <div className={styles.fields}>
        <NumberField
          label={`Вытеснять не раньше, чем через, ${unit}`}
          value={policy.min_runtime_before_preempt}
          onChange={(n) => set({ min_runtime_before_preempt: n ?? 0 })}
          hint="столько задача должна проработать, прежде чем её можно прервать"
        />
        <NumberField
          label={`Старение очереди, ${unit} на класс`}
          value={policy.aging}
          placeholder="выключено"
          onChange={(aging) => set({ aging })}
          hint="за каждые столько ожидания класс задачи растёт на 1; пусто — без старения"
        />
        <NumberField
          label="Прогноз перепланирования — до задач"
          value={policy.max_forecast_tasks}
          onChange={(n) => set({ max_forecast_tasks: n ?? 0 })}
          hint="на входах больше этого прогноз не строится; 0 — не строить вовсе"
        />
        <div className={styles.labelled}>
          <span className={styles.detail}>Проверка входа</span>
          <Segmented
            label="Проверка входа"
            options={VALIDATION}
            value={policy.validation_mode ?? "lenient"}
            onChange={(validation_mode) => set({ validation_mode })}
          />
          <span className={styles.detail}>строгая — при любой ошибке входа прогон не запускается</span>
        </div>
      </div>
    </Section>
  );
}
