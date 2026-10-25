import {
  type ButtonHTMLAttributes,
  type CSSProperties,
  type KeyboardEvent,
  type ReactNode,
  useId,
} from "react";

import { Link } from "react-router";

import { Icon, type IconName } from "../shell/Icon";
import styles from "./controls.module.css";

type NativeButton = Omit<ButtonHTMLAttributes<HTMLButtonElement>, "type">;

export function Button({
  variant = "primary",
  className,
  ...rest
}: NativeButton & { variant?: "primary" | "secondary" | "accent" }) {
  return (
    <button
      type="button"
      className={`${styles.button} ${styles[variant]} motion-lift ${className ?? ""}`}
      {...rest}
    />
  );
}

/** Переход, который выглядит как кнопка: «Объяснения», «Сравнить политики». */
export function ButtonLink({
  to,
  variant = "primary",
  children,
}: {
  to: string;
  variant?: "primary" | "secondary" | "accent";
  children: ReactNode;
}) {
  return (
    <Link to={to} className={`${styles.button} ${styles[variant]} ${styles.link} motion-lift`}>
      {children}
    </Link>
  );
}

/** Круглая кнопка с иконкой; подпись видна в подсказке и читается вслух. */
export function IconButton({
  label,
  icon,
  className,
  ...rest
}: NativeButton & { label: string; icon: IconName }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      className={`${styles.iconButton} motion-lift ${className ?? ""}`}
      {...rest}
    >
      <Icon name={icon} size={18} />
    </button>
  );
}

/** Чип-переключатель: фильтр или выбор из набора; `color` — фон во включённом виде. */
export function Chip({
  pressed,
  onToggle,
  color,
  children,
}: {
  pressed: boolean;
  onToggle: () => void;
  color?: string;
  children: ReactNode;
}) {
  const style = color ? ({ "--chip-on": color } as CSSProperties) : undefined;
  return (
    <button type="button" className={styles.chip} aria-pressed={pressed} onClick={onToggle} style={style}>
      {children}
    </button>
  );
}

export interface Option<T extends string> {
  value: T;
  label: string;
}

/** Выбор одного из нескольких; стрелки переводят выбор, Tab — к следующему элементу. */
export function Segmented<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: readonly Option<T>[];
  value: T;
  onChange: (value: T) => void;
}) {
  const move = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const delta = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key];
    if (delta === undefined) return;
    event.preventDefault();
    const next = (index + delta + options.length) % options.length;
    const option = options[next];
    if (!option) return;
    onChange(option.value);
    (event.currentTarget.parentElement?.children[next] as HTMLElement | undefined)?.focus();
  };
  return (
    <div role="radiogroup" aria-label={label} className={styles.segmented}>
      {options.map((option, index) => (
        <button
          key={option.value}
          type="button"
          role="radio"
          aria-checked={option.value === value}
          tabIndex={option.value === value ? 0 : -1}
          className={styles.segment}
          onClick={() => onChange(option.value)}
          onKeyDown={(event) => move(event, index)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

export function Switch({
  checked,
  onChange,
  label,
  hint,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: string;
  hint?: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      className={styles.switch}
      onClick={() => onChange(!checked)}
    >
      <span className={styles.switchText}>
        <span>{label}</span>
        {hint && <span className={styles.hint}>{hint}</span>}
      </span>
      <span className={styles.track} aria-hidden="true">
        <span className={styles.knob} />
      </span>
    </button>
  );
}

/** Число кнопками − и +, в пределах [min, max]. */
export function Stepper({
  label,
  value,
  onChange,
  min = 0,
  max = Infinity,
  step = 1,
  unit,
  less = "Меньше",
  more = "Больше",
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
  min?: number;
  max?: number;
  step?: number;
  unit?: string;
  less?: string;
  more?: string;
}) {
  // Округление убирает хвосты двоичной арифметики: 0.1 + 0.2 → 0.3
  const set = (next: number) => onChange(Math.min(max, Math.max(min, Math.round(next * 1e6) / 1e6)));
  return (
    <div role="group" aria-label={label} className={styles.stepper}>
      <span className={styles.stepperLabel}>{label}</span>
      <div className={styles.stepperRow}>
        <button
          type="button"
          aria-label={less}
          className={styles.step}
          disabled={value <= min}
          onClick={() => set(value - step)}
        >
          −
        </button>
        <output aria-live="polite" className={styles.stepperValue}>
          {value}
          {unit && <span className={styles.unit}> {unit}</span>}
        </output>
        <button
          type="button"
          aria-label={more}
          className={styles.step}
          disabled={value >= max}
          onClick={() => set(value + step)}
        >
          +
        </button>
      </div>
    </div>
  );
}

/** Целое число ≥ 0 или пусто; пустое поле — `null` (например, «без дедлайна» или «выключено»). */
export function NumberField({
  label,
  value,
  onChange,
  placeholder,
  hint,
}: {
  label: string;
  value: number | null | undefined;
  onChange: (value: number | null) => void;
  placeholder?: string;
  hint?: string;
}) {
  const id = useId();
  return (
    <div className={styles.numberField}>
      <label htmlFor={id} className={styles.numberLabel}>
        {label}
      </label>
      <input
        id={id}
        className={styles.numberInput}
        type="number"
        inputMode="numeric"
        min={0}
        value={value ?? ""}
        placeholder={placeholder}
        onChange={(e) =>
          onChange(e.target.value === "" ? null : Math.max(0, Math.round(Number(e.target.value))))
        }
      />
      {hint && <span className={styles.hint}>{hint}</span>}
    </div>
  );
}
