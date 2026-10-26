import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";

import { createQueryClient } from "./api/hooks";
import { App } from "./App";
import { EMPTY_DRAFT } from "./draft/draft";
import { DraftProvider } from "./draft/DraftContext";
import { sectionOf } from "./shell/sections";

function renderAt(path: string) {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <DraftProvider initial={EMPTY_DRAFT}>
        <MemoryRouter initialEntries={[path]}>
          <App />
        </MemoryRouter>
      </DraftProvider>
    </QueryClientProvider>,
  );
}

const heading = () => screen.getByRole("heading", { level: 1 });

describe("оболочка", () => {
  it("показывает все разделы и отмечает текущий", () => {
    renderAt("/builder");
    const nav = screen.getByRole("navigation", { name: "Разделы" });
    const labels = within(nav)
      .getAllByRole("link")
      .map((a) => a.getAttribute("aria-label"));
    expect(labels).toEqual([
      "expsched",
      "Обзор",
      "Сборка",
      "Кластер",
      "Прогон",
      "Сравнение",
      "Объяснения",
      "Контейнеры",
      "Дизайн-система",
    ]);
    expect(within(nav).getByRole("link", { name: "Сборка" })).toHaveAttribute("aria-current", "page");
    expect(heading()).toHaveTextContent("Сборка");
  });

  it("переходит по разделам без перезагрузки", async () => {
    renderAt("/");
    await userEvent.click(screen.getByRole("link", { name: "Дизайн-система" }));
    expect(heading()).toHaveTextContent("Общий язык интерфейса");
    expect(screen.getByRole("link", { name: "Дизайн-система" })).toHaveAttribute("aria-current", "page");
    await userEvent.click(screen.getByRole("link", { name: "Контейнеры" }));
    expect(heading()).toHaveTextContent("Контейнеры");
    expect(screen.getByRole("radio", { name: "Эмуляция" })).toBeInTheDocument();
  });

  it("незнакомый путь — не раздел", () => {
    renderAt("/nowhere");
    expect(heading()).toHaveTextContent("Страница не найдена");
    expect(screen.getByRole("navigation", { name: "Разделы" }).querySelector("[aria-current]")).toBeNull();
  });
});

describe("sectionOf", () => {
  it("узнаёт раздел и вложенные в него страницы", () => {
    expect(sectionOf("/")?.label).toBe("Обзор");
    expect(sectionOf("/runs/0123456789abcdef")?.label).toBe("Прогон");
    expect(sectionOf("/explain/0123456789abcdef")?.label).toBe("Объяснения");
    expect(sectionOf("/system")?.label).toBe("Дизайн-система");
    expect(sectionOf("/runsx")).toBeUndefined();
  });
});
