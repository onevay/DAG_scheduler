import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { useLive, useLiveEvents, useRunAction, useRunProblem, useStartOnAgents } from "../api/hooks";
import type { Container, Live } from "../api/types";
import {
  attemptLog,
  counts,
  FILTERS,
  type Filter,
  type Mode,
  MODES,
  phase,
  scaleLabel,
  SCALES,
  share,
  stateLabel,
  withoutAgent,
} from "../containers/containers";
import styles from "../containers/Containers.module.css";
import { useDraft } from "../draft/DraftContext";
import { Button, Segmented } from "../ui/controls";
import { Section } from "../ui/data";
import { PageHeader } from "../ui/PageHeader";

const UNITS = { hour: "ч", minute: "мин", second: "с" } as const;
const RING = 2 * Math.PI * 26;
const STATUS = { running: "идёт", done: "закончен", cancelled: "отменён" } as const;

/** Режим прогона и, если прогон на агентах идёт или прошёл, его контейнеры. */
export function ContainersPage() {
  const { runId } = useParams();
  const live = useLive(runId);
  return (
    <>
      <PageHeader
        title="Контейнеры"
        badge={
          live.data && (
            <span className={styles.badge} data-status={live.data.status}>
              <span className={`${styles.dot} motion-pulse`} aria-hidden="true" />
              {live.data.status === "running"
                ? `идёт · момент модели ${live.data.now}`
                : `${STATUS[live.data.status]} · момент модели ${live.data.now}`}
            </span>
          )
        }
      >
        Попытки задач в настоящих контейнерах на агентах: состояние, чекпоинты, отказы узлов.
      </PageHeader>
      <ModePanel />
      {runId && live.isError && (
        <p role="alert" className={styles.error}>
          {live.error.message}
        </p>
      )}
      {runId && live.data && <LiveRun runId={runId} live={live.data} />}
    </>
  );
}

/** Режим и масштаб времени черновика, назначение агента узлам и запуск. */
function ModePanel() {
  const { draft, dispatch } = useDraft();
  const navigate = useNavigate();
  const start = useStartOnAgents();
  const [agent, setAgent] = useState("laptop");
  const mode: Mode = draft.mode ?? "simulate";
  const scale = draft.time_scale_ms ?? 1000;
  const missing = withoutAgent(draft);
  const onAgents = mode === "emulate" || mode === "shadow";
  const set = (patch: Partial<typeof draft>) => dispatch({ type: "replace", draft: { ...draft, ...patch } });
  const assign = () =>
    set({ cluster: { workers: draft.cluster.workers.map((w) => ({ ...w, agent: w.agent || agent })) } });

  return (
    <Section title="Режим прогона">
      <Segmented
        label="Режим"
        options={(Object.keys(MODES) as Mode[]).map((m) => ({ value: m, label: MODES[m].label }))}
        value={mode}
        onChange={(next) => set({ mode: next })}
      />
      <p className={styles.note}>{MODES[mode].text}</p>
      {mode === "replay" && (
        <p className={styles.note}>
          Проигрывается сохранённый прогон — кнопка «Проиграть» на странице прогона.
        </p>
      )}
      {onAgents && (
        <div className={styles.controls}>
          <div className={styles.field}>
            <span className={styles.caption}>Масштаб: единица модели = {scaleLabel(scale)}</span>
            <Segmented
              label="Масштаб времени"
              options={SCALES.map((ms) => ({ value: String(ms), label: scaleLabel(ms) }))}
              value={String(scale)}
              onChange={(ms) => set({ time_scale_ms: Number(ms) })}
            />
          </div>
          {missing.length > 0 && (
            <div className={styles.field}>
              <span className={styles.caption}>Без агента: {missing.join(", ")}</span>
              <div className={styles.row}>
                <input
                  className={styles.input}
                  value={agent}
                  aria-label="Id агента"
                  onChange={(e) => setAgent(e.target.value.trim())}
                />
                <Button variant="secondary" disabled={!agent} onClick={assign}>
                  Назначить узлам
                </Button>
              </div>
            </div>
          )}
          <Button
            disabled={draft.tasks.length === 0 || missing.length > 0 || start.isPending}
            onClick={() =>
              start.mutate({ problem: draft }, { onSuccess: (r) => void navigate(`/containers/${r.run_id}`) })
            }
          >
            {start.isPending ? "Запускаю…" : "Запустить на агентах"}
          </Button>
          {start.isError && (
            <p role="alert" className={styles.error}>
              {start.error.message}
            </p>
          )}
        </div>
      )}
    </Section>
  );
}

function LiveRun({ runId, live }: { runId: string; live: Live }) {
  const problem = useRunProblem(runId);
  const events = useLiveEvents(runId, live.status === "running");
  const action = useRunAction(runId);
  const [filter, setFilter] = useState<Filter>("all");
  const [picked, setPicked] = useState<string | null>(null);
  const unit = UNITS[problem.data?.time_unit ?? "hour"];
  const durations = new Map(
    [...(problem.data?.tasks ?? []), ...(problem.data?.events ?? []).flatMap((e) => e.task ?? [])].map(
      (t) => [t.id, t.duration],
    ),
  );
  const key = (c: Container) => `${c.task}#${c.attempt}`;
  const shown = live.containers.filter((c) => filter === "all" || phase(c) === filter);
  const selected = live.containers.find((c) => key(c) === picked) ?? shown.at(-1);
  const n = counts(live.containers);
  const running = live.status === "running";

  return (
    <>
      <Section
        title={`Контейнеры · ${live.containers.length}`}
        actions={
          running ? (
            <Button variant="secondary" onClick={() => action.mutate({ kind: "cancel" })}>
              Отменить прогон
            </Button>
          ) : (
            live.status === "done" && <Link to={`/runs/${runId}`}>Открыть прогон</Link>
          )
        }
      >
        <div role="group" aria-label="Состояние" className={styles.filters}>
          {FILTERS.map((f) => (
            <button
              key={f.value}
              type="button"
              className={styles.filter}
              aria-pressed={filter === f.value}
              onClick={() => setFilter(f.value)}
            >
              {f.label} · {n[f.value]}
            </button>
          ))}
        </div>
        <div className={styles.cards}>
          {shown.map((c) => {
            const done = share(c, durations.get(c.task));
            return (
              <button
                key={key(c)}
                type="button"
                className={`${styles.card} motion-lift`}
                aria-pressed={selected !== undefined && key(selected) === key(c)}
                aria-label={`${c.task}, попытка ${c.attempt} на ${c.worker}: ${stateLabel(c)}, ${Math.round(done * 100)}%`}
                onClick={() => setPicked(key(c))}
              >
                <span className={styles.cardHead}>
                  <span className={styles.chip} data-phase={phase(c)}>
                    <span className={styles.chipDot} />
                    {stateLabel(c)}
                  </span>
                  <span className={styles.caption}>
                    {c.worker} · #{c.attempt}
                  </span>
                </span>
                <svg className={styles.ring} viewBox="0 0 64 64" aria-hidden="true">
                  <circle cx="32" cy="32" r="26" className={styles.track} />
                  <circle
                    cx="32"
                    cy="32"
                    r="26"
                    className={styles.arc}
                    data-phase={phase(c)}
                    strokeDasharray={RING}
                    strokeDashoffset={RING * (1 - done)}
                  />
                  <text x="32" y="36" textAnchor="middle" className={styles.pct}>
                    {Math.round(done * 100)}%
                  </text>
                </svg>
                <span className={styles.task}>{c.task}</span>
                <span className={styles.caption}>
                  {c.progress} из {durations.get(c.task) ?? "?"} {unit} работы сохранено · fence {c.attempt}
                </span>
              </button>
            );
          })}
        </div>
        {live.containers.length === 0 && (
          <p className={styles.note}>Попыток ещё нет: агент забирает команды.</p>
        )}
      </Section>

      {selected && (
        <Section title={`Выбранный контейнер · ${selected.task.toUpperCase()}`} tone="raised">
          <dl className={styles.facts}>
            <dt>Узел</dt>
            <dd>
              {selected.worker} (агент {selected.agent})
            </dd>
            <dt>Чекпоинт</dt>
            <dd>
              {selected.progress} из {durations.get(selected.task) ?? "?"} {unit}
            </dd>
            <dt>Fence</dt>
            <dd>{selected.attempt}</dd>
            <dt>Контейнер</dt>
            <dd>{selected.container_id ?? "—"}</dd>
          </dl>
          <ul className={styles.log} aria-label="Журнал попытки">
            {attemptLog(events.data ?? [], selected, unit).map((line) => (
              <li key={line}>{line}</li>
            ))}
            {selected.detail && <li>{selected.detail}</li>}
          </ul>
          {running && (
            <div className={styles.row}>
              <Button
                variant="secondary"
                disabled={phase(selected) !== "running"}
                onClick={() => action.mutate({ kind: "checkpoint", task: selected.task })}
              >
                Сохранить чекпоинт
              </Button>
              {live.workers[selected.worker]?.dropped ? (
                <Button onClick={() => action.mutate({ kind: "worker_up", worker: selected.worker })}>
                  Вернуть узел {selected.worker}
                </Button>
              ) : (
                <Button onClick={() => action.mutate({ kind: "worker_down", worker: selected.worker })}>
                  Уронить узел {selected.worker}
                </Button>
              )}
            </div>
          )}
          <p className={styles.note}>
            Fence — номер попытки: команды и события адресуются по нему, поэтому запоздавшее событие старой
            попытки после отказа узла не меняет новую.
          </p>
        </Section>
      )}
    </>
  );
}
