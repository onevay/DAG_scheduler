import { useState } from "react";

import type { Worker } from "../api/types";
import { useDraft } from "../draft/DraftContext";
import { Icon } from "../shell/Icon";
import styles from "./Cluster.module.css";
import { defaultOutage, type Outage } from "./timeline";
import { ResourceFields } from "../ui/ResourceFields";

/** Узел кластера: ядра, память и GPU (GPU ещё и слотами), метки для ограничений задач, отказ «одной кнопкой». */
export function WorkerCard({
  worker,
  down,
  horizon,
  unit,
}: {
  worker: Worker;
  down: readonly Outage[];
  horizon: number;
  unit: string;
}) {
  const { draft, dispatch } = useDraft();
  const [label, setLabel] = useState("");
  const gpu = worker.resources.gpu ?? 0;
  const labels = Object.entries(worker.labels ?? {});
  const failing = down.length > 0;
  const suggested = defaultOutage(horizon);

  const update = (patch: Partial<Worker>) => dispatch({ type: "updateWorker", id: worker.id, patch });
  const addLabel = () => {
    const [key, value] = label.split("=").map((s) => s.trim());
    if (!key || !value) return;
    update({ labels: { ...worker.labels, [key]: value } });
    setLabel("");
  };
  const dropNode = () => {
    dispatch({ type: "addEvent", event: { time: suggested.from, type: "worker_down", worker: worker.id } });
    dispatch({ type: "addEvent", event: { time: suggested.to, type: "worker_up", worker: worker.id } });
  };
  const restore = () =>
    dispatch({
      type: "replace",
      draft: { ...draft, events: draft.events?.filter((e) => e.worker !== worker.id) },
    });

  return (
    <section aria-label={worker.id} className={`${styles.card} motion-lift`} data-failing={failing}>
      <div className={styles.cardHead}>
        <span className={styles.cardId}>
          <span className={styles.cardIcon} aria-hidden="true">
            <Icon name="cluster" size={18} />
          </span>
          {worker.id}
        </span>
        <span className={styles.status} data-failing={failing}>
          <span className={styles.dot} aria-hidden="true" />
          {failing
            ? `падает ${down.map((o) => `${o.from}–${o.to}`).join(", ")} ${unit}`
            : "всё время в строю"}
        </span>
      </div>

      <ResourceFields value={worker.resources} onChange={(resources) => update({ resources })} of="узла" />
      {gpu > 0 && (
        <div className={styles.slots} aria-hidden="true">
          {Array.from({ length: Math.max(4, gpu) }, (_, i) => (
            <span key={i} className={`${styles.slot} motion-data`} data-on={i < gpu} data-failing={failing} />
          ))}
        </div>
      )}

      <div className={styles.labels}>
        {labels.map(([k, v]) => (
          <button
            key={k}
            type="button"
            className={styles.label}
            aria-label={`Убрать метку ${k}=${v}`}
            onClick={() => update({ labels: Object.fromEntries(labels.filter(([key]) => key !== k)) })}
          >
            {k}={v} ×
          </button>
        ))}
        <input
          className={styles.labelInput}
          value={label}
          placeholder="+ метка gpu=a100"
          aria-label={`Новая метка узла ${worker.id}`}
          onChange={(e) => setLabel(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && addLabel()}
          onBlur={addLabel}
        />
      </div>

      <div className={styles.cardFoot}>
        <button
          type="button"
          className={styles.danger}
          onClick={() => dispatch({ type: "removeWorker", id: worker.id })}
        >
          Удалить узел
        </button>
        {failing ? (
          <button type="button" className={styles.soft} onClick={restore}>
            Убрать отказы
          </button>
        ) : (
          <button type="button" className={styles.strong} onClick={dropNode}>
            Уронить {suggested.from}–{suggested.to} {unit}
          </button>
        )}
      </div>
    </section>
  );
}
