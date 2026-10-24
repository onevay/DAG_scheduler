import styles from "./App.module.css";
import { Rail } from "./shell/Rail";
import { sectionOf } from "./shell/sections";

export function App({ pathname = window.location.pathname }: { pathname?: string }) {
  const section = sectionOf(pathname);
  return (
    <div className={styles.page}>
      <Rail pathname={pathname} />
      <main className={styles.main}>
        <h1 className={`${styles.title} motion-rise`}>{section?.label ?? "Страница не найдена"}</h1>
        <p className={styles.note}>Экран появится на следующих шагах.</p>
      </main>
    </div>
  );
}
