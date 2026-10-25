import type { IconName } from "./Icon";

export interface Section {
  path: string;
  label: string;
  icon: IconName;
}

// Порядок — путь пользователя: от обзора к сборке, прогону и разбору результата.
export const SECTIONS: readonly Section[] = [
  { path: "/", label: "Обзор", icon: "home" },
  { path: "/builder", label: "Сборка", icon: "builder" },
  { path: "/cluster", label: "Кластер", icon: "cluster" },
  { path: "/runs", label: "Прогон", icon: "run" },
  { path: "/compare", label: "Сравнение", icon: "compare" },
  { path: "/explain", label: "Объяснения", icon: "explain" },
  { path: "/containers", label: "Контейнеры", icon: "containers" },
];

export const FOOTER_SECTIONS: readonly Section[] = [
  { path: "/system", label: "Дизайн-система", icon: "system" },
];

/** Раздел, к которому относится путь: сам раздел или вложенная в него страница (`/runs/…`). */
export function sectionOf(pathname: string): Section | undefined {
  return [...SECTIONS, ...FOOTER_SECTIONS].find((s) =>
    s.path === "/" ? pathname === "/" : pathname === s.path || pathname.startsWith(`${s.path}/`),
  );
}
