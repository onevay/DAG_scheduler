import type { ReactNode } from "react";

import styles from "./PageHeader.module.css";

/** Заголовок экрана и короткое пояснение справа; `badge` — метка над заголовком. */
export function PageHeader({
  title,
  badge,
  children,
}: {
  title: string;
  badge?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <header className={`${styles.header} motion-rise`}>
      <div className={styles.titles}>
        {badge}
        <h1 className={styles.title}>{title}</h1>
      </div>
      {children && <p className={styles.note}>{children}</p>}
    </header>
  );
}
