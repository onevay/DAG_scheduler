import { useState } from "react";

import { usePolicies, usePolicyRuns } from "../api/hooks";
import type { PolicyName, Problem, Result } from "../api/types";
import { capsules, insight, METRICS, type MetricKey, profile } from "../compare/compare";
import styles from "../compare/Compare.module.css";
import { Flower } from "../compare/Flower";
import { WhatIf } from "../compare/WhatIf";
import { useDraft } from "../draft/DraftContext";
import { comparisonRows } from "../run/results";
import { Button, ButtonLink, Segmented } from "../ui/controls";
import { PageHeader } from "../ui/PageHeader";
import { plural, POLICIES } from "../ui/plural";

const UNITS = { hour: "ч", minute: "мин", second: "с" } as const;
const METRIC_OPTIONS = (Object.keys(METRICS) as MetricKey[]).map((k) => ({
  value: k,
  label: METRICS[k].label,
}));

export function ComparePage() {
  const { draft } = useDraft();
  // Сравнивается снимок черновика: правки в «Сборке» не перезапускают прогоны сами, только по кнопке
  const [problem, setProblem] = useState<Problem | undefined>(draft.tasks.length > 0 ? draft : undefined);
  // Номер снимка: новый снимок — новый список вмешательств, предложенный по нему
  const [snapshot, setSnapshot] = useState(0);
  const policies = usePolicies();
  const names: PolicyName[] = policies.data?.policies ?? [];
  const runs = usePolicyRuns(problem, names);
  const [metric, setMetric] = useState<MetricKey>("makespan");
  const [picked, setPicked] = useState<string | null>(null);

  const done = runs.flatMap((r, i) => (r.data ? [[names[i] as string, r.data] as const] : []));
  const rows = comparisonRows(
    Object.fromEntries(done.map(([name, run]) => [name, run.result])) as Record<string, Result>,
  );
  const failed = runs.flatMap((r, i) => (r.error ? [`${names[i]}: ${r.error.message}`] : []));
  const pending = runs.some((r) => r.isFetching);
  const selected = picked ?? rows[0]?.policy ?? null;
  const runOf = new Map(done.map(([name, run]) => [name, run.run_id]));
  const unit = UNITS[problem?.time_unit ?? "hour"];
  const stale = problem !== undefined && problem !== draft;

  return (
    <>
      <PageHeader
        title="Сравнение политик"
        badge={
          <span className={styles.crumb}>
            {problem
              ? `${problem.name ?? "черновик"} — ${plural(names.length, POLICIES)}, детерминированно`
              : "черновик пуст"}
          </span>
        }
        actions={
          <Button
            variant={stale ? "primary" : "secondary"}
            disabled={draft.tasks.length === 0 || !stale}
            onClick={() => {
              setProblem(draft);
              setSnapshot(snapshot + 1);
            }}
          >
            {stale ? "Пересчитать по черновику" : "Черновик не менялся"}
          </Button>
        }
      >
        Один вход под каждой реализованной политикой; любой прогон открывается целиком.
      </PageHeader>

      {!problem ? (
        <p className={styles.note}>Черновик пуст — соберите вход в «Сборке» или загрузите пример.</p>
      ) : (
        <div className={styles.layout}>
          <section aria-labelledby="compare-metric" className={styles.panel}>
            <div className={styles.head}>
              <h2 id="compare-metric" className={styles.h2}>
                {METRICS[metric].title(unit)}
              </h2>
              <Segmented label="Метрика" options={METRIC_OPTIONS} value={metric} onChange={setMetric} />
            </div>
            <div className={styles.capsules} aria-busy={pending}>
              {capsules(rows, metric).map((c) => (
                <button
                  key={c.policy}
                  type="button"
                  className={styles.capsule}
                  aria-pressed={c.policy === selected}
                  aria-label={`${c.policy}: ${c.value === null ? "нет метрик" : METRICS[metric].format(c.value, unit)}`}
                  onClick={() => setPicked(c.policy)}
                >
                  <span className={styles.pill}>
                    {c.value === null ? "—" : METRICS[metric].format(c.value, unit)}
                  </span>
                  <span
                    className={`${styles.bar} motion-data`}
                    data-best={c.best}
                    style={{ height: `${Math.max(14, Math.round(c.height * 230))}px` }}
                  >
                    <span className={styles.cap} />
                  </span>
                  <span className={styles.name}>{c.policy}</span>
                </button>
              ))}
            </div>
            {pending && <p className={styles.note}>Считаю прогоны…</p>}
            {failed.map((f) => (
              <p key={f} role="alert" className={styles.error}>
                {f}
              </p>
            ))}
            {rows.length > 0 && <p className={styles.insight}>{insight(rows, metric, unit)}</p>}
          </section>

          {selected && (
            <section aria-labelledby="compare-profile" className={`${styles.panel} ${styles.profile}`}>
              <div className={styles.head}>
                <h2 id="compare-profile" className={styles.h2}>
                  Профиль
                </h2>
                <span className={styles.policy}>{selected}</span>
              </div>
              <Flower policy={selected} scores={profile(rows, selected)} />
              <p className={styles.note}>
                Чем длиннее лепесток, тем лучше: короче расписание, выше загрузка, меньше ожидания и потерь.
                100% — лучшая среди политик.
              </p>
              {runOf.get(selected) && (
                <ButtonLink to={`/runs/${runOf.get(selected)}`}>Открыть прогон {selected}</ButtonLink>
              )}
            </section>
          )}
        </div>
      )}
      {problem && (
        <WhatIf key={snapshot} problem={problem} policy={selected as PolicyName | null} unit={unit} />
      )}
    </>
  );
}
