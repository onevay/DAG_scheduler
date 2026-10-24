import {
  createContext,
  type Dispatch,
  type ReactNode,
  useContext,
  useEffect,
  useReducer,
  useState,
} from "react";

import { useValidation } from "../api/hooks";
import { type Draft, type DraftAction, draftReducer, loadDraft, saveDraft } from "./draft";

interface DraftState {
  draft: Draft;
  dispatch: Dispatch<DraftAction>;
}

const DraftContext = createContext<DraftState | null>(null);

/** Черновик входа, общий для сборки, кластера и сравнения; переживает перезагрузку страницы. */
export function DraftProvider({ initial, children }: { initial?: Draft; children: ReactNode }) {
  const [draft, dispatch] = useReducer(draftReducer, initial, (start) => start ?? loadDraft());
  useEffect(() => saveDraft(draft), [draft]);
  return <DraftContext.Provider value={{ draft, dispatch }}>{children}</DraftContext.Provider>;
}

export function useDraft(): DraftState {
  const state = useContext(DraftContext);
  if (!state) throw new Error("useDraft вне DraftProvider");
  return state;
}

/** Значение, которое меняется не чаще раза в `delay` мс: пока ввод идёт, держится прежнее. */
export function useDebounced<T>(value: T, delay: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return settled;
}

/** Проверка черновика через API, когда ввод затих; пустой черновик не проверяется. */
export function useDraftValidation(delay = 400) {
  const { draft } = useDraft();
  const settled = useDebounced(draft, delay);
  return useValidation(settled.tasks.length > 0 ? settled : undefined);
}
