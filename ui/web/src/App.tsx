import { lazy, Suspense } from "react";
import { Link, Navigate, Route, Routes, useLocation } from "react-router";

import { useRuns } from "./api/hooks";
import styles from "./App.module.css";
import { ClusterPage } from "./pages/ClusterPage";
import { ComparePage } from "./pages/ComparePage";
import { ContainersPage } from "./pages/ContainersPage";
import { ExplainPage } from "./pages/ExplainPage";
import { OverviewPage } from "./pages/OverviewPage";
import { RunPage } from "./pages/RunPage";
import { SystemPage } from "./pages/SystemPage";
import { ErrorBoundary } from "./shell/ErrorBoundary";
import { Rail } from "./shell/Rail";
import { PageHeader } from "./ui/PageHeader";

// Сборка тянет React Flow — это больше трети всего кода; грузится отдельно, когда открывают раздел
const BuilderPage = lazy(() => import("./pages/BuilderPage").then((m) => ({ default: m.BuilderPage })));

export function App() {
  const { pathname } = useLocation();
  return (
    <div className={styles.page}>
      <Rail />
      <main className={styles.main}>
        {/* Ключ по адресу: переход в другой раздел сбрасывает ошибку прошлого экрана */}
        <ErrorBoundary key={pathname}>
          <Routes>
            <Route path="/" element={<OverviewPage />} />
            <Route
              path="/builder"
              element={
                <Suspense fallback={<PageHeader title="Сборка">Загружаю редактор…</PageHeader>}>
                  <BuilderPage />
                </Suspense>
              }
            />
            <Route path="/cluster" element={<ClusterPage />} />
            <Route path="/runs" element={<LatestRun to="runs" title="Прогон" />} />
            <Route path="/runs/:runId" element={<RunPage />} />
            <Route path="/compare" element={<ComparePage />} />
            <Route path="/explain" element={<LatestRun to="explain" title="Объяснения" />} />
            <Route path="/explain/:runId" element={<ExplainPage />} />
            <Route path="/system" element={<SystemPage />} />
            <Route path="/containers" element={<ContainersPage />} />
            <Route
              path="*"
              element={
                <PageHeader title="Страница не найдена">
                  Такого адреса нет — выберите раздел слева.
                </PageHeader>
              }
            />
          </Routes>
        </ErrorBoundary>
      </main>
    </div>
  );
}

/** Разделы «Прогон» и «Объяснения» без id открывают самый свежий прогон. */
function LatestRun({ to, title }: { to: "runs" | "explain"; title: string }) {
  const runs = useRuns(1);
  const latest = runs.data?.[0];
  if (latest) return <Navigate to={`/${to}/${latest.run_id}`} replace />;
  return (
    <PageHeader title={title}>
      {runs.isPending ? "Ищу последний прогон…" : "Прогонов пока нет — "}
      {!runs.isPending && <Link to="/">начните с примера в обзоре</Link>}
    </PageHeader>
  );
}
