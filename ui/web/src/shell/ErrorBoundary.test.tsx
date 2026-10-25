import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ErrorBoundary } from "./ErrorBoundary";

function Broken(): never {
  throw new Error("tasks is not iterable");
}

describe("ErrorBoundary", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows the error with a way out instead of a blank page", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    // Ошибка брошена нарочно: jsdom не должен печатать её как необработанную
    const silence = (event: ErrorEvent) => event.preventDefault();
    window.addEventListener("error", silence);
    const reload = vi.fn();
    vi.spyOn(window, "location", "get").mockReturnValue({ ...window.location, reload });
    localStorage.setItem("expsched.draft.v1", "{broken");
    render(
      <ErrorBoundary>
        <Broken />
      </ErrorBoundary>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("tasks is not iterable");
    await userEvent.click(screen.getByRole("button", { name: "Сбросить черновик" }));
    expect(localStorage.getItem("expsched.draft.v1")).toBeNull();
    expect(reload).toHaveBeenCalledOnce();
    window.removeEventListener("error", silence);
  });

  it("renders children when nothing breaks", () => {
    render(
      <ErrorBoundary>
        <p>экран</p>
      </ErrorBoundary>,
    );
    expect(screen.getByText("экран")).toBeInTheDocument();
  });
});
