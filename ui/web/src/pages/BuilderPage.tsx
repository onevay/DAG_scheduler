import { type ChangeEvent, useMemo, useState } from "react";
import { useNavigate } from "react-router";

import { api } from "../api/client";
import { useCreateRun, useScenarios, useStartOnAgents } from "../api/hooks";
import type { Validation } from "../api/types";
import { Canvas } from "../builder/Canvas";
import { CheckStrip, PolicySettings } from "../builder/Checks";
import { TaskPanel } from "../builder/TaskPanel";
import { diagnosticsByTask, EMPTY_DRAFT } from "../draft/draft";
import { useDraft, useDraftValidation } from "../draft/DraftContext";
import { downloadDraft, readDraft } from "../draft/files";
import { Button } from "../ui/controls";
import { Section } from "../ui/data";
import { PageHeader } from "../ui/PageHeader";
import styles from "./BuilderPage.module.css";
import { EVENTS, NODES, plural, TASKS } from "../ui/plural";

export function BuilderPage() {
  const { draft } = useDraft();
  const validation = useDraftValidation();
  const [selected, setSelected] = useState<string | null>(null);
  const analysis = validation.data?.analysis;
  // Рамка ошибки — только у задач с ошибками; предупреждения видны в итоге проверки
  const invalid = useMemo(() => {
    const groups = diagnosticsByTask(draft, validation.data?.diagnostics ?? []);
    return new Set([...groups].filter(([, ds]) => ds.some((d) => d.level === "error")).map(([task]) => task));
  }, [draft, validation.data]);
  return (
    <>
      <PageHeader title="Сборка" actions={<RunButton validation={validation.data} />}>
        Задачи, зависимости и узлы эксперимента; проверка идёт по ходу ввода.
      </PageHeader>
      <DraftToolbar />
      <div className={styles.layout}>
        <div className={styles.main}>
          <Canvas
            selected={selected}
            onSelect={setSelected}
            criticalPath={analysis?.critical_path ?? []}
            invalid={invalid}
            lowerBound={analysis?.lower_bound}
          />
          <CheckStrip validation={validation} onSelect={setSelected} />
        </div>
        <TaskPanel selected={selected} onSelect={setSelected} />
      </div>
      <PolicySettings />
    </>
  );
}

/** Запуск черновика: прогон открывается на своей странице, прогон на агентах — на экране «Контейнеры». */
function RunButton({ validation }: { validation: Validation | undefined }) {
  const { draft } = useDraft();
  const navigate = useNavigate();
  const run = useCreateRun();
  const start = useStartOnAgents();
  const onAgents = draft.mode === "emulate" || draft.mode === "shadow";
  const blocked = draft.tasks.length === 0 || validation?.valid === false || draft.mode === "replay";
  const pending = run.isPending || start.isPending;
  const error = run.error ?? start.error;
  const launch = () =>
    onAgents
      ? start.mutate({ problem: draft }, { onSuccess: (r) => void navigate(`/containers/${r.run_id}`) })
      : run.mutate({ problem: draft }, { onSuccess: (r) => void navigate(`/runs/${r.run_id}`) });
  return (
    <div className={styles.run}>
      <Button
        disabled={blocked || pending}
        title={
          draft.mode === "replay"
            ? "Replay проигрывает сохранённый прогон, а не черновик"
            : blocked
              ? "Сначала исправьте ошибки входа"
              : undefined
        }
        onClick={launch}
      >
        {pending ? "Запускаю…" : onAgents ? "Запустить на агентах" : "Запустить"}
      </Button>
      {error && (
        <p role="alert" className={styles.error}>
          {error.message}
        </p>
      )}
    </div>
  );
}

/** Откуда взять черновик и куда его отдать: пример, файл, очистка, скачивание. */
function DraftToolbar() {
  const { draft, dispatch } = useDraft();
  const scenarios = useScenarios();
  const [error, setError] = useState<string | null>(null);

  const load = async (name: string) => {
    if (!name) return;
    try {
      dispatch({ type: "replace", draft: await api.scenario(name) });
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };
  const importFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = ""; // тот же файл можно выбрать снова
    if (!file) return;
    try {
      dispatch({ type: "replace", draft: await readDraft(file) });
      setError(null);
    } catch (e) {
      setError(`${file.name}: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  return (
    <Section title={draft.name ?? "Новый эксперимент"}>
      <div className={styles.toolbar}>
        <label className={styles.field}>
          <span>Пример</span>
          <select value="" onChange={(e) => void load(e.target.value)} disabled={!scenarios.data}>
            <option value="">{scenarios.isError ? "API недоступен" : "выбрать…"}</option>
            {scenarios.data?.map((s) => (
              <option key={s.name} value={s.name}>
                {s.name} · {plural(s.tasks, TASKS)} · {plural(s.workers, NODES)}
              </option>
            ))}
          </select>
        </label>
        <label className={styles.upload}>
          Открыть problem.json
          <input type="file" accept="application/json,.json" onChange={(e) => void importFile(e)} />
        </label>
        <Button variant="secondary" onClick={() => downloadDraft(draft)} disabled={draft.tasks.length === 0}>
          Скачать
        </Button>
        <Button variant="secondary" onClick={() => dispatch({ type: "replace", draft: EMPTY_DRAFT })}>
          Очистить
        </Button>
      </div>
      <p className={styles.counts}>
        {plural(draft.tasks.length, TASKS)} · {plural(draft.cluster.workers.length, NODES)} ·{" "}
        {plural(draft.events?.length ?? 0, EVENTS)}
      </p>
      {error && (
        <p role="alert" className={styles.error}>
          {error}
        </p>
      )}
    </Section>
  );
}
