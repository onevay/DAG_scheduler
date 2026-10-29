import { Stepper } from "./controls";
import styles from "./controls.module.css";
import type { Resources } from "./resources";

/**
 * Ядра, ОЗУ и GPU задачи или узла. ОЗУ показывается в ГБ с шагом 0,5 и хранится в МиБ; нулевой ресурс
 * убирается из словаря, остальные ресурсы (свои имена) сохраняются как есть.
 */
export function ResourceFields({
  value,
  onChange,
  of,
}: {
  value: Resources | undefined;
  onChange: (next: Record<string, number>) => void;
  /** Чьи ресурсы — для подписей кнопок: «задачи», «узла». */
  of: string;
}) {
  const set = (r: string, n: number) =>
    onChange(Object.fromEntries(Object.entries({ ...value, [r]: n }).filter(([, v]) => v > 0)));
  return (
    <div className={styles.resources}>
      <Stepper
        label="CPU"
        value={value?.cpu ?? 0}
        max={256}
        onChange={(n) => set("cpu", n)}
        less={`Меньше ядер ${of}`}
        more={`Больше ядер ${of}`}
      />
      <Stepper
        label="ОЗУ"
        unit="ГБ"
        value={(value?.ram_mb ?? 0) / 1024}
        step={0.5}
        max={2048}
        onChange={(gb) => set("ram_mb", Math.round(gb * 1024))}
        less={`Меньше памяти ${of}`}
        more={`Больше памяти ${of}`}
      />
      <Stepper
        label="GPU"
        value={value?.gpu ?? 0}
        max={16}
        onChange={(n) => set("gpu", n)}
        less={`Меньше GPU ${of}`}
        more={`Больше GPU ${of}`}
      />
    </div>
  );
}
