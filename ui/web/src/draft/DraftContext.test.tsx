import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useDebounced, useDraft, DraftProvider } from "./DraftContext";
import { loadDraft } from "./draft";

describe("useDebounced", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("keeps the previous value until input settles", () => {
    const { result, rerender } = renderHook(({ value }) => useDebounced(value, 400), {
      initialProps: { value: "a" },
    });
    rerender({ value: "ab" });
    act(() => vi.advanceTimersByTime(300));
    rerender({ value: "abc" });
    act(() => vi.advanceTimersByTime(300));
    expect(result.current).toBe("a");
    act(() => vi.advanceTimersByTime(100));
    expect(result.current).toBe("abc");
  });
});

describe("DraftProvider", () => {
  beforeEach(() => localStorage.clear());

  it("saves every change to localStorage", () => {
    const wrapper = ({ children }: { children: ReactNode }) => <DraftProvider>{children}</DraftProvider>;
    const { result } = renderHook(() => useDraft(), { wrapper });
    act(() => result.current.dispatch({ type: "addTask" }));
    expect(result.current.draft.tasks.map((t) => t.id)).toEqual(["task_1"]);
    expect(loadDraft().tasks.map((t) => t.id)).toEqual(["task_1"]);
  });
});
