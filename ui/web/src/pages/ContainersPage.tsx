import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";

import {
  useAgents,
  useContainerLogs,
  useLive,
  useLiveRuns,
  useLocalAgentControl,
  useLocalAgents,
  useLiveEvents,
  useRunAction,
  useRunProblem,
  useStartOnAgents,
} from "../api/hooks";
import type { AgentInfo, Container, Live, LocalAgent, LocalAgentStart } from "../api/types";
import {
  attemptLog,
  counts,
  expectation,
  FILTERS,
  type Filter,
  type Mode,
  MODES,
  phase,
  scaleLabel,
  SCALES,
  share,
  stateLabel,
  usageMeters,
  withoutAgent,
} from "../containers/containers";
import styles from "../containers/Containers.module.css";
import { PlanVsFact } from "../containers/PlanVsFact";
import { SOURCES } from "../builder/DurationFields";
import { useDraft } from "../draft/DraftContext";
import { Button, Segmented, Switch } from "../ui/controls";
import { Section } from "../ui/data";
import { ResourceFields } from "../ui/ResourceFields";
import { PageHeader } from "../ui/PageHeader";
import { plural } from "../ui/plural";
import { formatResources, type Resources } from "../ui/resources";

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
      <AgentsPanel />
      {!runId && <LiveRuns />}
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
  const known = useAgents().data?.agents ?? [];
  const [agent, setAgent] = useState("laptop");
  const mode: Mode = draft.mode ?? "simulate";
  const scale = draft.time_scale_ms ?? 1000;
  const missing = withoutAgent(draft);
  const onAgents = mode === "emulate" || mode === "shadow";
  const set = (patch: Partial<typeof draft>) => dispatch({ type: "replace", draft: { ...draft, ...patch } });
  const assign = () =>
    set({ cluster: { workers: draft.cluster.workers.map((w) => ({ ...w, agent: w.agent || agent })) } });
  const choose = (worker: string, next: string) =>
    set({
      cluster: {
        workers: draft.cluster.workers.map((w) => (w.id === worker ? { ...w, agent: next || undefined } : w)),
      },
    });

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
          Проигрывается сохранённый прогон — кнопка «Проиграть журнал» на странице прогона.
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
          {known.length > 0 && (
            <div role="group" aria-label="Агенты узлов" className={styles.nodes}>
              {draft.cluster.workers.map((w) => (
                <label key={w.id} className={styles.node}>
                  <span className={styles.task}>{w.id}</span>
                  <select
                    className={styles.input}
                    aria-label={`Агент узла ${w.id}`}
                    value={w.agent ?? ""}
                    onChange={(e) => choose(w.id, e.target.value)}
                  >
                    <option value="">без агента</option>
                    {[...new Set([...known.map((a) => a.id), ...(w.agent ? [w.agent] : [])])].map((id) => {
                      const a = known.find((k) => k.id === id);
                      return (
                        <option key={id} value={id}>
                          {id}
                          {!a ? " — сервер его не видел" : a.alive ? "" : " — нет связи"}
                        </option>
                      );
                    })}
                  </select>
                </label>
              ))}
            </div>
          )}
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

/** Агенты, представившиеся серверу: на связи ли, что отдают под задачи, сколько контейнеров заняты. */
function AgentsPanel() {
  const agents = useAgents();
  const locals = useLocalAgents().data ?? [];
  const control = useLocalAgentControl();
  const known = agents.data?.agents ?? [];
  // Запущенный только что ещё не представился серверу — карточка есть и у него
  const ids = [...new Set([...known.map((a) => a.id), ...locals.map((l) => l.id)])].sort();
  return (
    <Section title={`Агенты · ${ids.length}`}>
      {ids.length === 0 ? (
        <p className={styles.note}>
          Ни один агент ещё не представился серверу. Запустите его здесь, на машине сервера, или на другой
          машине: <code>python -m expsched.agent --id laptop --scheduler {window.location.origin}</code> с тем
          же EXPSCHED_AGENT_TOKEN, что у сервера (подробно — docs/agent.md).
        </p>
      ) : (
        <ul className={styles.cards} aria-label="Агенты">
          {ids.map((id) => (
            <AgentCard
              key={id}
              id={id}
              agent={known.find((a) => a.id === id)}
              local={locals.find((l) => l.id === id)}
              onStop={() => control.stop.mutate(id)}
              stopping={control.stop.isPending && control.stop.variables === id}
            />
          ))}
        </ul>
      )}
      <LocalAgentForm taken={ids} start={control.start} />
    </Section>
  );
}

function AgentCard({
  id,
  agent: a,
  local,
  onStop,
  stopping,
}: {
  id: string;
  agent: AgentInfo | undefined;
  local: LocalAgent | undefined;
  onStop: () => void;
  stopping: boolean;
}) {
  const exited = local && !local.alive;
  const short = a?.alive
    ? "на связи"
    : exited
      ? "процесс завершился"
      : local && !a
        ? "запускается"
        : "нет связи";
  const state = a?.alive
    ? "на связи"
    : exited
      ? `процесс завершился, код ${local.exit_code}`
      : local && !a
        ? "запускается"
        : `нет связи · ${Math.round(a?.seen_s_ago ?? 0)} с`;
  return (
    <li className={styles.card} aria-label={`Агент ${id}: ${short}`}>
      <span className={styles.cardHead}>
        <span className={styles.task}>{id}</span>
        <span
          className={styles.chip}
          data-phase={a?.alive ? "running" : local && !exited ? "waiting" : "killed"}
        >
          <span className={styles.chipDot} />
          {state}
        </span>
      </span>
      <dl className={styles.facts}>
        <dt>Среда</dt>
        <dd>{a?.runtime ?? local?.runtime}</dd>
        {a && (
          <>
            <dt>Отдаёт</dt>
            <dd>{formatResources(a.capacity)}</dd>
            <dt>Контейнеры</dt>
            <dd>
              {a.running.length} из {a.max_containers}
            </dd>
          </>
        )}
        {local && (
          <>
            <dt>Процесс</dt>
            <dd>на этой машине · pid {local.pid}</dd>
          </>
        )}
      </dl>
      {local && (
        <>
          <details className={styles.details}>
            <summary>Журнал процесса</summary>
            <pre className={styles.console}>{local.log.join("\n") || "пусто"}</pre>
          </details>
          <Button
            variant="secondary"
            disabled={stopping}
            onClick={onStop}
            aria-label={`Остановить агента ${id}`}
          >
            {stopping ? "Останавливаю…" : exited ? "Убрать" : "Остановить"}
          </Button>
        </>
      )}
    </li>
  );
}

type AgentRuntime = NonNullable<LocalAgentStart["runtime"]>;

const RUNTIMES = [
  { value: "docker", label: "Docker" },
  { value: "podman", label: "Podman" },
  { value: "fake", label: "Без контейнеров" },
] as const;

/** Запуск агента процессом рядом с сервером: перед запуском сервер проверяет среду (`agent check`). */
function LocalAgentForm({
  taken,
  start,
}: {
  taken: string[];
  start: ReturnType<typeof useLocalAgentControl>["start"];
}) {
  const free = ["laptop", ...Array.from({ length: 8 }, (_, i) => `local-${i + 1}`)].find(
    (n) => !taken.includes(n),
  );
  const [id, setId] = useState("");
  const [runtime, setRuntime] = useState<AgentRuntime>("docker");
  const [auto, setAuto] = useState(true);
  const [capacity, setCapacity] = useState<Record<string, number>>({ cpu: 4, ram_mb: 8192 });
  const name = id || free || "";
  const submit = () =>
    start.mutate({ id: name, runtime, ...(auto ? {} : capacity) }, { onSuccess: () => setId("") });
  return (
    <form
      className={styles.controls}
      aria-label="Запустить агента на этой машине"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <span className={styles.caption}>Запустить агента на этой машине</span>
      <div className={styles.row}>
        <input
          className={styles.input}
          aria-label="Id нового агента"
          placeholder={free}
          value={id}
          onChange={(e) => setId(e.target.value.trim())}
        />
        <Segmented
          label="Среда агента"
          options={[...RUNTIMES]}
          value={runtime}
          onChange={(v) => setRuntime(v as AgentRuntime)}
        />
      </div>
      <Switch
        checked={auto}
        onChange={setAuto}
        label="Ёмкость автоматически"
        hint="машина минус одно ядро и 2 ГБ памяти"
      />
      {!auto && <ResourceFields value={capacity} onChange={setCapacity} of="агента" />}
      <Button disabled={!name || start.isPending} onClick={submit}>
        {start.isPending ? "Проверяю и запускаю…" : `Запустить ${name}`}
      </Button>
      {start.isError && (
        <pre role="alert" className={styles.console}>
          {start.error.message}
        </pre>
      )}
    </form>
  );
}

/** Прогоны на агентах, которые помнит сервер, — чтобы вернуться к идущему или посмотреть законченный. */
function LiveRuns() {
  const runs = useLiveRuns().data ?? [];
  if (runs.length === 0) return null;
  return (
    <Section title="Прогоны на агентах">
      <ul className={styles.runs}>
        {runs.map((r) => (
          <li key={r.run_id}>
            <Link to={`/containers/${r.run_id}`}>{r.name ?? r.run_id}</Link>
            <span className={styles.caption}>
              {" "}
              · {MODES[r.mode].label} · {STATUS[r.status]} · момент модели {r.now} · идут {r.running} из{" "}
              {r.containers}
            </span>
          </li>
        ))}
      </ul>
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
  const tasks = [
    ...(problem.data?.tasks ?? []),
    ...(problem.data?.events ?? []).flatMap((e) => e.task ?? []),
  ];
  const durations = new Map(tasks.map((t) => [t.id, t.duration]));
  // Без длительности — оценка сервера, уточнённая по ходу попытки
  const expect = (c: Container) =>
    expectation(events.data ?? [], c, durations.get(c.task), live.estimates?.[c.task]);
  const workText = (c: Container) => {
    const e = expect(c);
    return e ? `${e.approx ? "≈ " : ""}${Math.round(e.expected)}` : "?";
  };
  // Закончившейся попытке с оценкой ожидание уже не нужно: сколько ждали — для сравнения
  const caption = (c: Container) =>
    c.state === "exited" && !c.detail && expect(c)?.approx
      ? `закончена · ждали ${workText(c)} ${unit} работы`
      : `${c.progress} из ${workText(c)} ${unit} работы сохранено`;
  const limits = new Map<string, Resources | undefined>(tasks.map((t) => [t.id, t.resources]));
  const key = (c: Container) => `${c.task}#${c.attempt}`;
  const shown = live.containers.filter((c) => filter === "all" || phase(c) === filter);
  const selected = live.containers.find((c) => key(c) === picked) ?? shown.at(-1);
  const guess = selected && expect(selected);
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
            const e = expect(c);
            const done = share(c, e?.expected);
            // Кольцо — до p80: дуга — сделанное, полоса в конце — где задача закончится скорее всего
            const scale = e && e.p80 > 0 ? e.p80 : 1;
            const made = c.state === "exited" && !c.detail ? 1 : Math.min(1, c.progress / scale);
            const pct = `${e?.approx && done < 1 ? "≈" : ""}${Math.round(done * 100)}%`;
            return (
              <button
                key={key(c)}
                type="button"
                className={`${styles.card} motion-lift`}
                aria-pressed={selected !== undefined && key(selected) === key(c)}
                aria-label={`${c.task}, попытка ${c.attempt} на ${c.worker}: ${stateLabel(c)}, ${pct}`}
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
                  {e?.approx && e.p80 > e.expected && phase(c) !== "done" && (
                    <circle
                      cx="32"
                      cy="32"
                      r="26"
                      className={styles.band}
                      data-testid="p80-band"
                      strokeDasharray={`${RING * (1 - e.expected / e.p80)} ${RING}`}
                      strokeDashoffset={-RING * (e.expected / e.p80)}
                    />
                  )}
                  <circle
                    cx="32"
                    cy="32"
                    r="26"
                    className={styles.arc}
                    data-phase={phase(c)}
                    strokeDasharray={RING}
                    strokeDashoffset={RING * (1 - (e?.approx ? made : done))}
                  />
                  <text x="32" y="36" textAnchor="middle" className={styles.pct}>
                    {pct}
                  </text>
                </svg>
                <span className={styles.task}>{c.task}</span>
                <span className={styles.caption}>
                  {caption(c)} · fence {c.attempt}
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
              {selected.progress} из {workText(selected)} {unit}
            </dd>
            {guess?.approx && (
              <>
                <dt>Оценка</dt>
                <dd>
                  ≈ {Math.round(guess.expected)} {unit}, до {guess.p80} {unit} с вероятностью 80 % ·{" "}
                  {SOURCES[live.estimates?.[selected.task]?.source ?? "kind"]}
                </dd>
              </>
            )}
            <dt>Fence</dt>
            <dd>{selected.attempt}</dd>
            <dt>Контейнер</dt>
            <dd>{selected.container_id ?? "—"}</dd>
          </dl>
          {selected.usage && (
            <div role="group" aria-label="Потребление" className={styles.meters}>
              {usageMeters(selected.usage, limits.get(selected.task)).map((m) => (
                <UsageMeter key={m.label} meter={m} />
              ))}
            </div>
          )}
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
          <ContainerLog
            key={key(selected)}
            runId={runId}
            container={selected}
            follow={running && phase(selected) !== "done" && phase(selected) !== "killed"}
          />
          <p className={styles.note}>
            Fence — номер попытки: команды и события адресуются по нему, поэтому запоздавшее событие старой
            попытки после отказа узла не меняет новую.
          </p>
        </Section>
      )}

      {live.shadow && <PlanVsFact shadow={live.shadow} unit={unit} />}
    </>
  );
}

function UsageMeter({ meter }: { meter: ReturnType<typeof usageMeters>[number] }) {
  const pct = meter.share === null ? null : Math.round(meter.share * 100);
  return (
    <>
      <span className={styles.caption}>{meter.label}</span>
      <div
        role="meter"
        aria-label={`${meter.label}: ${meter.text}`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct ?? 0}
        className={styles.bar}
      >
        <div
          className={styles.fill}
          data-near={meter.near}
          style={{ width: `${Math.min(100, pct ?? 0)}%` }}
        />
      </div>
      <span>
        {meter.text}
        {meter.near && " · у предела памяти"}
      </span>
    </>
  );
}

const LINES = ["строка", "строки", "строк"] as const;

/** Хвост журнала контейнера: обновляется, пока попытка идёт; пауза держит его на месте для чтения. */
function ContainerLog({
  runId,
  container,
  follow,
}: {
  runId: string;
  container: Container;
  follow: boolean;
}) {
  const [paused, setPaused] = useState(false);
  const [copied, setCopied] = useState(false);
  const logs = useContainerLogs(runId, container.task, container.attempt, follow && !paused);
  const lines = logs.data ?? [];
  const box = useRef<HTMLPreElement>(null);
  useEffect(() => {
    if (!paused && box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [lines.length, paused]);
  const copy = () =>
    void navigator.clipboard?.writeText(lines.join("\n")).then(
      () => setCopied(true),
      () => setCopied(false),
    );
  return (
    <div className={styles.field}>
      <div className={styles.row}>
        <span className={styles.caption}>Журнал контейнера · {plural(lines.length, LINES)}</span>
        {follow && (
          <Button variant="secondary" aria-pressed={paused} onClick={() => setPaused(!paused)}>
            {paused ? "Продолжить" : "Пауза"}
          </Button>
        )}
        <Button variant="secondary" disabled={lines.length === 0} onClick={copy}>
          {copied ? "Скопировано" : "Копировать"}
        </Button>
      </div>
      {lines.length > 0 ? (
        <pre ref={box} tabIndex={0} role="region" aria-label="Журнал контейнера" className={styles.console}>
          {lines.join("\n")}
        </pre>
      ) : (
        <p className={styles.note}>
          Строк нет: агент ещё не прислал их, или среда журнала не отдаёт (gravesentinel).
        </p>
      )}
    </div>
  );
}
