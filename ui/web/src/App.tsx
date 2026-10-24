import { Route, Routes, useLocation } from "react-router";

import styles from "./App.module.css";
import { BuilderPage } from "./pages/BuilderPage";
import { ClusterPage } from "./pages/ClusterPage";
import { ContainersPage } from "./pages/ContainersPage";
import { RunPage } from "./pages/RunPage";
import { SystemPage } from "./pages/SystemPage";
import { Rail } from "./shell/Rail";
import { sectionOf } from "./shell/sections";
import { PageHeader } from "./ui/PageHeader";

export function App() {
  return (
    <div className={styles.page}>
      <Rail />
      <main className={styles.main}>
        <Routes>
          <Route path="/builder" element={<BuilderPage />} />
          <Route path="/cluster" element={<ClusterPage />} />
          <Route path="/runs/:runId" element={<RunPage />} />
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
