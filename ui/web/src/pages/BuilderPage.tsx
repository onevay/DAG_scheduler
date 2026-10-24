import { type ChangeEvent, useMemo, useState } from "react";

import { api } from "../api/client";
import { useScenarios } from "../api/hooks";
import { Canvas } from "../builder/Canvas";
import { diagnosticsByTask, EMPTY_DRAFT } from "../draft/draft";
import { useDraft, useDraftValidation } from "../draft/DraftContext";
import { downloadDraft, readDraft } from "../draft/files";
import { Button } from "../ui/controls";
import { Section } from "../ui/data";
import { PageHeader } from "../ui/PageHeader";
import styles from "./BuilderPage.module.css";

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
      <PageHeader title="Сборка">
        Задачи, зависимости и узлы эксперимента; проверка идёт по ходу ввода.
      </PageHeader>
      <DraftToolbar />
      <Canvas
        selected={selected}
        onSelect={setSelected}
        criticalPath={analysis?.critical_path ?? []}
        invalid={invalid}
        lowerBound={analysis?.lower_bound}
      />
      <ValidationSummary />
    </>
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
                {s.name} · {s.tasks} задач · {s.workers} узлов
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
        {draft.tasks.length} задач · {draft.cluster.workers.length} узлов · {draft.events?.length ?? 0}{" "}
        событий
      </p>
      {error && (
        <p role="alert" className={styles.error}>
          {error}
        </p>
      )}
    </Section>
  );
}

function ValidationSummary() {
  const { draft } = useDraft();
  const validation = useDraftValidation();
  if (draft.tasks.length === 0) return null;
  if (validation.isError)
    return <p className={styles.error}>Проверка не удалась: {validation.error.message}</p>;
  if (!validation.data) return <p className={styles.counts}>Проверка…</p>;

  const { valid, diagnostics, analysis } = validation.data;
  const groups = [...diagnosticsByTask(draft, diagnostics)];
  return (
    <Section title={valid ? "Вход корректен" : "Есть ошибки"} tone={valid ? "accent" : "raised"}>
      {analysis && <p className={styles.counts}>Нижняя граница makespan: {analysis.lower_bound}</p>}
      <ul className={styles.groups} aria-busy={validation.isFetching}>
        {groups.map(([task, items]) => (
          <li key={task}>
            <code>{task || "весь вход"}</code>
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
    </Section>
  );
}
