import { Section } from "../ui/data";
import { PageHeader } from "../ui/PageHeader";
import styles from "./pages.module.css";

/** Раздел следующего этапа: что в нём будет, пока планировщик работает только с моделью кластера. */
export function ContainersPage() {
  return (
    <>
      <PageHeader
        title="Контейнеры"
        badge={
          <span className={styles.badge}>
            <span className={`${styles.live} motion-pulse`} aria-hidden="true" />
            следующий этап
          </span>
        }
      >
        Запуск задач плана в настоящих контейнерах: план рядом с фактом, логи и ресурсы каждой попытки.
      </PageHeader>
      <Section title="Что здесь появится">
        <ul className={styles.list}>
          <li>режимы emulate и shadow: план выполняется в контейнерах или идёт рядом с реальной очередью;</li>
          <li>сравнение плана с фактом по каждой задаче: старт, длительность, причина расхождения;</li>
          <li>журнал контейнера рядом с журналом планировщика;</li>
          <li>воспроизведение (replay) записанного прогона.</li>
        </ul>
      </Section>
    </>
  );
}
