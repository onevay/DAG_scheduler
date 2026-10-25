import { EventsBoard } from "../cluster/EventsBoard";
import styles from "../cluster/Cluster.module.css";
import { horizonOf, outages } from "../cluster/timeline";
import { WorkerCard } from "../cluster/WorkerCard";
import { useDraft, useDraftValidation } from "../draft/DraftContext";
import { Icon } from "../shell/Icon";
import { Button } from "../ui/controls";
import { PageHeader } from "../ui/PageHeader";

const UNITS = { hour: "ч", minute: "мин", second: "с" } as const;

export function ClusterPage() {
  const { draft, dispatch } = useDraft();
  const validation = useDraftValidation();
  const events = draft.events ?? [];
  const horizon = horizonOf(events, validation.data?.analysis?.lower_bound);
  const down = outages(events, horizon);
  const unit = UNITS[draft.time_unit ?? "hour"];
  const gpu = draft.cluster.workers.reduce((s, w) => s + (w.resources.gpu ?? 0), 0);
  const add = () => dispatch({ type: "addWorker" });

  return (
    <>
      <PageHeader title={`Кластер · ${gpu} GPU`} actions={<Button onClick={add}>Добавить узел</Button>}>
        Узлы черновика и события на них; прогон учтёт изменения после «Запустить» в «Сборке».
      </PageHeader>
      <div className={styles.cards}>
        {draft.cluster.workers.map((w) => (
          <WorkerCard
            key={w.id}
            worker={w}
            down={down.filter((o) => o.worker === w.id)}
            horizon={horizon}
            unit={unit}
          />
        ))}
        <button type="button" className={`${styles.newCard} motion-lift`} onClick={add}>
          <span className={styles.newIcon} aria-hidden="true">
            <Icon name="plus" />
          </span>
          Новый узел
        </button>
      </div>
      <EventsBoard down={down} horizon={horizon} unit={unit} />
    </>
  );
}
