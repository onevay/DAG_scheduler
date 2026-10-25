import { useState } from "react";

import type { TaskKind, TaskStatus } from "../api/types";
import { Button, Chip, IconButton, Segmented, Stepper, Switch } from "../ui/controls";
import { Feed, KindSwatch, MetricCard, PillMeter, Section, StatusChip } from "../ui/data";
import { PageHeader } from "../ui/PageHeader";
import styles from "./pages.module.css";

const COLORS = [
  ["Фон", "--color-bg"],
  ["Карточка", "--color-surface"],
  ["Вложенная", "--color-surface-raised"],
  ["Чернила, главное действие", "--color-ink"],
  ["Акцент: выбрано, лучшее", "--color-accent"],
  ["Чертёж", "--color-blueprint"],
  ["Фокус на чертеже", "--color-blueprint-focus"],
  ["Вторичный текст", "--color-muted"],
  ["Успех", "--color-success"],
  ["Предупреждение", "--color-warning"],
  ["Ошибка", "--color-error"],
  ["Срочная задача", "--color-urgent"],
] as const;

const KINDS: readonly TaskKind[] = ["data", "train", "eval", "infer", "other"];
const STATUSES: readonly TaskStatus[] = ["SUCCEEDED", "READY", "RUNNING", "FAILED", "BLOCKED", "INFEASIBLE"];
const PRIORITY = [
  { value: "low", label: "low" },
  { value: "normal", label: "normal" },
  { value: "high", label: "high" },
] as const;

const FEED = [
  { key: "1", time: "14 ч", type: "WORKER_DOWN", text: "node-C упал" },
  { key: "2", time: "14 ч", type: "REPLAN", text: "перепланирование: 3 задачи" },
  { key: "3", time: "12 ч", type: "START", text: "eval_asr на node-B" },
];

/** Живая дизайн-система: токены и компоненты в настоящем коде, со всеми состояниями. */
export function SystemPage() {
  const [priority, setPriority] = useState<(typeof PRIORITY)[number]["value"]>("normal");
  const [preemptible, setPreemptible] = useState(true);
  const [kind, setKind] = useState<TaskKind>("train");
  const [hours, setHours] = useState(6);
  return (
    <>
      <PageHeader title="Общий язык интерфейса">
        Светлая мягкая основа для работы, тёмный «чертёж» для структуры: графа, событий, объяснений.
      </PageHeader>

      <div className={styles.row}>
        <Section title="Цвета" className={styles.wide}>
          <ul className={styles.swatches}>
            {COLORS.map(([label, token]) => (
              <li key={token} className={styles.swatch}>
                <span className={styles.sample} style={{ background: `var(${token})` }} />
                <span className={styles.swatchLabel}>{label}</span>
                <code className={styles.token}>{token}</code>
              </li>
            ))}
          </ul>
          <div className={styles.inline}>
            <span className={styles.caption}>Типы задач</span>
            {KINDS.map((k) => (
              <KindSwatch key={k} kind={k} />
            ))}
          </div>
        </Section>

        <Section title="Шрифты" tone="raised" className={styles.narrow}>
          <span className={styles.metricSample}>22 ч</span>
          <span className={styles.caption}>Manrope 300 — крупные числа метрик</span>
          <span className={styles.techSample}>DAG · 21 Ч</span>
          <span className={styles.caption}>Barlow Condensed — только на чертеже: размеры, итоги</span>
          <code className={styles.monoSample}>pretrain_vit · node-B · WAIT_DEPS</code>
          <span className={styles.caption}>JetBrains Mono — id задач, узлов, коды, время</span>
        </Section>
      </div>

      <Section title="Компоненты">
        <div className={styles.inline}>
          <Button>Главное действие</Button>
          <Button variant="secondary">Второстепенное</Button>
          <Button variant="accent">Акцентное</Button>
          <IconButton label="Сравнить" icon="compare" />
          <Button disabled>Недоступно</Button>
          <Segmented label="Приоритет" options={PRIORITY} value={priority} onChange={setPriority} />
        </div>
        <div className={styles.inline}>
          {KINDS.map((k) => (
            <Chip key={k} pressed={kind === k} onToggle={() => setKind(k)} color={`var(--kind-${k})`}>
              {k}
            </Chip>
          ))}
        </div>
        <div className={styles.row}>
          <Switch
            checked={preemptible}
            onChange={setPreemptible}
            label="Можно вытеснить"
            hint="ради срочной задачи класса выше"
          />
          <Stepper
            label="Длительность"
            unit="ч"
            value={hours}
            onChange={setHours}
            min={1}
            max={48}
            less="Короче"
            more="Дольше"
          />
        </div>
        <div className={styles.row}>
          <MetricCard label="Метрика с шагами" value={48} unit="%">
            <PillMeter share={0.48} />
          </MetricCard>
          <div className={styles.card}>
            <div className={styles.inline}>
              {STATUSES.map((s) => (
                <StatusChip key={s} status={s} />
              ))}
            </div>
            <span className={styles.caption}>
              Статус всегда с цветом и словом: цвет не единственный носитель смысла
            </span>
          </div>
          <Feed title="Журнал до этого момента" items={FEED} />
        </div>
      </Section>

      <Section title="Движение" tone="ink">
        <p className={styles.text}>
          Появление — 0.7 с, подъём на 10 px с затуханием, каскад 80 мс. Наведение — 0.35 с, карточка
          поднимается на 3 px. Данные — 0.7 с на высоту и ширину полос. Пульс — только у того, что идёт прямо
          сейчас. Кривая одна: cubic-bezier(.2,.8,.2,1). При «уменьшить движение» в системе всё мгновенно.
        </p>
      </Section>
    </>
  );
}
