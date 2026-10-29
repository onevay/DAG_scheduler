import type { components } from "../api/schema";
import type { Task } from "../api/types";
import { NumberField, Segmented, Stepper } from "../ui/controls";
import styles from "./TaskPanel.module.css";

type Estimate = components["schemas"]["EstimateOut"];
type Law = "none" | "amdahl" | "power";

/** Откуда взята оценка работы — так, как это видно пользователю. */
export const SOURCES: Record<Estimate["source"], string> = {
  estimate: "ваша оценка",
  history: "по истории прогонов",
  similar: "по задачам того же типа",
  kind: "умолчание для типа",
};

/** Медиана, которую задал пользователь: число или `{median, spread}`. */
export function ownEstimate(task: Task): { median: number | null; spread: number | null } {
  const e = task.estimate;
  if (e === null || e === undefined) return { median: null, spread: null };
  return typeof e === "number" ? { median: e, spread: null } : { median: e.median, spread: e.spread ?? null };
}

/** Длительность для подписи: число, «≈ медиана» у оценки или «?», если оценки нет. */
export function durationText(task: Task): string {
  if (task.duration !== null && task.duration !== undefined) return String(task.duration);
  const { median } = ownEstimate(task);
  return median === null ? "?" : `≈ ${median}`;
}

/** Длительность задачи: известна — число, неизвестна — оценка и закон ускорения по числу её CPU/GPU. */
export function DurationFields({
  task,
  unit,
  estimate,
  agents,
  update,
}: {
  task: Task;
  unit: string;
  estimate: Estimate | undefined;
  agents: boolean;
  update: (patch: Partial<Task>) => void;
}) {
  const known = task.duration !== null && task.duration !== undefined;
  const own = ownEstimate(task);
  const setEstimate = (median: number | null, spread: number | null) =>
    update({ estimate: median === null ? null : spread === null ? median : { median, spread } });
  const law: Law = task.scaling?.law ?? "none";

  return (
    <>
      <Segmented
        label="Длительность"
        options={[
          { value: "known", label: "известна" },
          { value: "unknown", label: "неизвестна" },
        ]}
        value={known ? "known" : "unknown"}
        onChange={(v) =>
          v === "known"
            ? update({
                duration: Math.max(1, Math.round(own.median ?? estimate?.median ?? 1)),
                estimate: null,
              })
            : update({ duration: null })
        }
      />
      {known ? (
        <div className={styles.pair}>
          <Stepper
            label="Длительность"
            unit={unit}
            value={task.duration ?? 1}
            min={1}
            onChange={(duration) => update({ duration })}
            less="Короче"
            more="Дольше"
          />
        </div>
      ) : (
        <>
          <div className={styles.pair}>
            <NumberField
              label={`Оценка, медиана, ${unit}`}
              value={own.median}
              placeholder="из истории"
              onChange={(median) => setEstimate(median || null, own.spread)}
            />
            <NumberField
              label="Разброс σ"
              value={own.spread}
              step={0.05}
              max={3}
              placeholder="по типу"
              onChange={(spread) => setEstimate(own.median, spread)}
            />
          </div>
          {agents ? (
            <p className={styles.hint} aria-label="Оценка до запуска">
              {estimate
                ? `≈ ${round(estimate.mean)} ${unit} в среднем, до ${round(estimate.p80)} ${unit} с вероятностью 80 % · ${SOURCES[estimate.source]}`
                : "оценка появится после проверки"}
            </p>
          ) : (
            <p className={styles.hint} data-tone="warn">
              без длительности задача идёт только на агентах: выберите эмуляцию или shadow на «Контейнерах»
            </p>
          )}
        </>
      )}
      <div className={styles.pair}>
        <Segmented
          label="Закон ускорения"
          options={[
            { value: "none", label: "по умолчанию" },
            { value: "amdahl", label: "Амдал" },
            { value: "power", label: "степенной" },
          ]}
          value={law}
          onChange={(next) =>
            update({
              scaling:
                next === "none"
                  ? null
                  : next === "amdahl"
                    ? { law: "amdahl", serial: 0.1 }
                    : { law: "power", alpha: 0.9 },
            })
          }
        />
        {law === "amdahl" && (
          <NumberField
            label="Последовательная доля"
            value={task.scaling?.serial}
            step={0.01}
            max={1}
            onChange={(serial) => update({ scaling: { law: "amdahl", serial } })}
          />
        )}
        {law === "power" && (
          <NumberField
            label="Показатель α"
            value={task.scaling?.alpha}
            step={0.05}
            max={1.5}
            onChange={(alpha) => update({ scaling: { law: "power", alpha } })}
          />
        )}
      </div>
      <p className={styles.hint}>
        Закон переносит наблюдённую длительность на другое число CPU/GPU — по нему учится история прогонов.
      </p>
    </>
  );
}

const round = (x: number) => Math.round(x * 10) / 10;
