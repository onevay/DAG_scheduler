import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { App } from "./App";
import { sectionOf } from "./shell/sections";

describe("оболочка", () => {
  it("показывает все разделы и отмечает текущий", () => {
    render(<App pathname="/builder" />);
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
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Сборка");
  });

  it("незнакомый путь — не раздел", () => {
    render(<App pathname="/nowhere" />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Страница не найдена");
  });
});

describe("sectionOf", () => {
  it("узнаёт раздел и вложенные в него страницы", () => {
    expect(sectionOf("/")?.label).toBe("Обзор");
    expect(sectionOf("/runs/0123456789abcdef")?.label).toBe("Прогон");
    expect(sectionOf("/system")?.label).toBe("Дизайн-система");
    expect(sectionOf("/runsx")).toBeUndefined();
  });
});
