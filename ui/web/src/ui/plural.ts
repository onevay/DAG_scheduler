const RULES = new Intl.PluralRules("ru");

/** Число со словом в нужной форме: 1 задача, 3 задачи, 11 задач. Формы — для 1, 2–4 и 5. */
export function plural(n: number, [one, few, many]: readonly [string, string, string]): string {
  const form = RULES.select(n);
  return `${n} ${form === "one" ? one : form === "few" ? few : many}`;
}

export const TASKS = ["задача", "задачи", "задач"] as const;
export const NODES = ["узел", "узла", "узлов"] as const;
export const EVENTS = ["событие", "события", "событий"] as const;
export const EDGES = ["связь", "связи", "связей"] as const;
export const POLICIES = ["политика", "политики", "политик"] as const;
export const VARIANTS = ["вариант", "варианта", "вариантов"] as const;
