import { useState } from "react";
import { Link, useNavigate } from "react-router";

import { api } from "../api/client";
import { useScenarios } from "../api/hooks";
import { EMPTY_DRAFT } from "../draft/draft";
import { useDraft } from "../draft/DraftContext";
import { NODES, plural, TASKS } from "../ui/plural";
import styles from "./Overview.module.css";

/** Новый сценарий: пример из каталога или пустой черновик — и сразу в «Сборку». */
export function NewScenario({ wide = false }: { wide?: boolean }) {
  const { draft, dispatch } = useDraft();
  const navigate = useNavigate();
  const scenarios = useScenarios();
  const [error, setError] = useState<string | null>(null);

  const start = async (name: string | null) => {
    try {
      dispatch({ type: "replace", draft: name ? await api.scenario(name) : EMPTY_DRAFT });
      void navigate("/builder");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <section aria-labelledby="overview-new" className={styles.newCard} data-wide={wide}>
      <h2 id="overview-new" className={styles.newTitle}>
        Соберите DAG и посмотрите, как политика разложит его по узлам
      </h2>
      <div className={styles.examples}>
        {scenarios.data?.map((s) => (
          <button key={s.name} type="button" className={styles.example} onClick={() => void start(s.name)}>
            <span>{s.name}</span>
            <span className={styles.exampleMeta}>
              {plural(s.tasks, TASKS)} · {plural(s.workers, NODES)}
            </span>
          </button>
        ))}
        {scenarios.isError && (
          <span className={styles.exampleMeta}>Примеры недоступны: {scenarios.error.message}</span>
        )}
      </div>
      {/* Пример и пустой черновик заменяют текущий — поэтому рядом путь вернуться к нему */}
      <div className={styles.examples}>
        {draft.tasks.length > 0 && (
          <Link to="/builder" className={styles.blank}>
            Продолжить черновик · {plural(draft.tasks.length, TASKS)}
          </Link>
        )}
        <button type="button" className={styles.blank} onClick={() => void start(null)}>
          Пустой черновик
        </button>
      </div>
      {error && (
        <p role="alert" className={styles.exampleMeta}>
          {error}
        </p>
      )}
    </section>
  );
}
