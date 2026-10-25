import { useState } from "react";
import { useNavigate } from "react-router";

import { ApiError } from "../api/client";
import { useAddEvent } from "../api/hooks";
import type { InputEvent, Problem } from "../api/types";
import { freeId } from "../draft/draft";
import { Button, Segmented, Stepper } from "../ui/controls";
import styles from "./Run.module.css";

type Action = "drop" | "submit";
const ACTIONS = [
  { value: "drop", label: "уронить узел" },
  { value: "submit", label: "подать задачу" },
] as const;

/**
 * «А если в этот момент…»: событие в момент `t` к входу прогона — API считает новый прогон, он открывается со
 * ссылкой на исходный. Отказ с возвратом — два события подряд: возврат добавляется к прогону с отказом.
 */
export function RunEvents({
  runId,
  problem,
  t,
  unit,
}: {
  runId: string;
  problem: Problem;
  t: number;
  unit: string;
}) {
  const navigate = useNavigate();
  const addEvent = useAddEvent();
  const [action, setAction] = useState<Action>("drop");
  const workers = problem.cluster.workers.map((w) => w.id);
  const [worker, setWorker] = useState(workers[0] ?? "");
  const [downFor, setDownFor] = useState(0);
  const [duration, setDuration] = useState(2);
  const [gpu, setGpu] = useState(1);
  const [priority, setPriority] = useState(10);
  const [deadline, setDeadline] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | Error | null>(null);
  // Время событий входа — целое
  const at = Math.round(t);

  const events = (): InputEvent[] => {
    if (action === "drop") {
      const down: InputEvent = { time: at, type: "worker_down", worker };
      return downFor > 0 ? [down, { time: at + downFor, type: "worker_up", worker }] : [down];
    }
    const taken = [
      ...problem.tasks.map((x) => x.id),
      ...(problem.events ?? []).flatMap((e) => e.task?.id ?? []),
    ];
    const task = {
      id: freeId("urgent", taken),
      duration,
      resources: { gpu },
      priority,
      deadline: deadline === "" ? null : Math.max(at, Math.round(Number(deadline))),
    };
    return [{ time: at, type: "submit_task", task }];
  };

  const apply = async () => {
    setBusy(true);
    setError(null);
    try {
      let current = runId;
      for (const event of events()) current = (await addEvent.mutateAsync({ runId: current, event })).run_id;
      void navigate(`/runs/${current}?from=${runId}`);
    } catch (e) {
      setError(e instanceof Error ? e : new Error(String(e)));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div role="group" aria-label="Событие в этот момент" className={styles.whatif}>
      <span className={styles.cardLabel}>
        А если в момент {at} {unit}…
      </span>
      <Segmented label="Что сделать" options={ACTIONS} value={action} onChange={setAction} />
      {action === "drop" ? (
        <div className={styles.whatifRow}>
          <label className={styles.field}>
            <span className={styles.cardLabel}>Узел</span>
            <select value={worker} onChange={(e) => setWorker(e.target.value)}>
              {workers.map((w) => (
                <option key={w} value={w}>
                  {w}
                </option>
              ))}
            </select>
          </label>
          <Stepper
            label={`Вернётся через, ${unit} (0 — нет)`}
            value={downFor}
            min={0}
            onChange={setDownFor}
            less="Раньше"
            more="Позже"
          />
        </div>
      ) : (
        <div className={styles.whatifRow}>
          <Stepper
            label="Длительность"
            unit={unit}
            value={duration}
            min={1}
            onChange={setDuration}
            less="Короче"
            more="Дольше"
          />
          <Stepper
            label="GPU"
            value={gpu}
            min={0}
            max={16}
            onChange={setGpu}
            less="Меньше GPU"
            more="Больше GPU"
          />
          <Stepper
            label="Приоритет"
            value={priority}
            min={0}
            max={10}
            onChange={setPriority}
            less="Ниже приоритет"
            more="Выше приоритет"
          />
          <label className={styles.field}>
            <span className={styles.cardLabel}>Дедлайн, {unit}</span>
            <input
              type="number"
              min={at}
              value={deadline}
              placeholder="нет"
              onChange={(e) => setDeadline(e.target.value)}
            />
          </label>
        </div>
      )}
      <div className={styles.whatifRow}>
        <Button onClick={() => void apply()} disabled={busy || (action === "drop" && !worker)}>
          {busy ? "Пересчитываю…" : "Пересчитать прогон"}
        </Button>
        <span className={styles.muted}>Исходный прогон не меняется — откроется новый.</span>
      </div>
      {error && (
        <div role="alert" className={styles.error}>
          {error.message}
          {error instanceof ApiError &&
            error.diagnostics.map((d, i) => (
              <span key={i}>
                <code>{d.code}</code> {d.hint}
              </span>
            ))}
        </div>
      )}
    </div>
  );
}
