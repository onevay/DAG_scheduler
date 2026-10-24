import { lazy, Suspense } from "react";
import { Route, Routes, useLocation } from "react-router";

import styles from "./App.module.css";
import { ClusterPage } from "./pages/ClusterPage";
import { ComparePage } from "./pages/ComparePage";
import { ContainersPage } from "./pages/ContainersPage";
import { ExplainPage } from "./pages/ExplainPage";
import { RunPage } from "./pages/RunPage";
import { SystemPage } from "./pages/SystemPage";
import { Rail } from "./shell/Rail";
import { sectionOf } from "./shell/sections";
import { PageHeader } from "./ui/PageHeader";

// Сборка тянет React Flow — это больше трети всего кода; грузится отдельно, когда открывают раздел
const BuilderPage = lazy(() => import("./pages/BuilderPage").then((m) => ({ default: m.BuilderPage })));

export function App() {
  return (
    <div className={styles.page}>
      <Rail />
      <main className={styles.main}>
        <Routes>
          <Route
            path="/builder"
            element={
              <Suspense fallback={<PageHeader title="Сборка">Загружаю редактор…</PageHeader>}>
                <BuilderPage />
              </Suspense>
            }
          />
          <Route path="/cluster" element={<ClusterPage />} />
          <Route path="/runs/:runId" element={<RunPage />} />
          <Route path="/compare" element={<ComparePage />} />
          <Route path="/explain/:runId" element={<ExplainPage />} />
          <Route path="/system" element={<SystemPage />} />
          <Route path="/containers" element={<ContainersPage />} />
          <Route path="*" element={<Placeholder />} />
        </Routes>
      </main>
    </div>
  );
}

/** Раздел, экран которого ещё не сделан, или незнакомый путь. */
function Placeholder() {
  const section = sectionOf(useLocation().pathname);
  return (
    <PageHeader title={section?.label ?? "Страница не найдена"}>
      {section ? "Экран появится на следующих шагах." : "Такого адреса нет — выберите раздел слева."}
    </PageHeader>
  );
}
