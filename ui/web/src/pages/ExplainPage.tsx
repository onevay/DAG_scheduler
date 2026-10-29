import { useParams } from "react-router";

import { ApiError } from "../api/client";
import { useRun, useRunProblem } from "../api/hooks";
import { Blueprint } from "../explain/Blueprint";
import { IdleMap } from "../explain/IdleMap";
import { ButtonLink } from "../ui/controls";
import { PageHeader } from "../ui/PageHeader";
import styles from "./RunPage.module.css";

const UNITS = { hour: "ч", minute: "мин", second: "с" } as const;

/** Почему makespan такой: разложение по критической цепочке и простой GPU — всё из журнала прогона. */
export function ExplainPage() {
  const { runId = "" } = useParams();
  const run = useRun(runId);
  const problem = useRunProblem(runId);

  if (run.isError || problem.isError) {
    const error = run.error ?? problem.error;
    const missing = error instanceof ApiError && error.status === 404;
    return (
      <PageHeader title={missing ? "Прогон не найден" : "Объяснение не загрузилось"}>
        {missing ? `Прогона ${runId} нет в хранилище.` : error?.message}
      </PageHeader>
    );
  }
  if (!run.data || !problem.data) return <PageHeader title="Объяснения">Загружаю прогон {runId}…</PageHeader>;

  const { result } = run.data;
  const unit = UNITS[problem.data.time_unit ?? "hour"];
  const makespan = result.metrics?.makespan;
  const crumb = (
    <span className={styles.crumb}>
      {problem.data.name ?? "прогон"} · {result.policy} · ответ построен по журналу прогона
    </span>
  );
  const actions = (
    <div className={styles.actions}>
      <ButtonLink to={`/runs/${runId}`} variant="secondary">
        К прогону
      </ButtonLink>
      <ButtonLink to="/compare">Что если…</ButtonLink>
    </div>
  );

  if (!result.explain || makespan === undefined) {
    return (
      <PageHeader title="Объяснения" badge={crumb} actions={actions}>
        Прогон не дошёл до расписания ({result.status}) — объяснять нечего.
      </PageHeader>
    );
  }
  const workers = problem.data.cluster.workers.map((w) => ({ id: w.id, capacity: w.resources }));
  return (
    <>
      <PageHeader title={`Почему ${makespan} ${unit}`} badge={crumb} actions={actions} />
      <Blueprint explain={result.explain} makespan={makespan} unit={unit} />
      <IdleMap idle={result.explain.idle} workers={workers} horizon={makespan} unit={unit} />
    </>
  );
}
