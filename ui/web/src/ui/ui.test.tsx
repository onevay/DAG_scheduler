import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { Chip, Segmented, Stepper, Switch } from "./controls";
import { Feed, PillMeter, StatusChip } from "./data";

const OPTIONS = [
  { value: "a", label: "Первый" },
  { value: "b", label: "Второй" },
  { value: "c", label: "Третий" },
] as const;

function Controlled() {
  const [value, setValue] = useState<"a" | "b" | "c">("a");
  const [on, setOn] = useState(false);
  const [n, setN] = useState(1);
  const [pressed, setPressed] = useState(false);
  return (
    <>
      <Segmented label="Выбор" options={OPTIONS} value={value} onChange={setValue} />
      <Switch label="Вытеснять" hint="ради срочных" checked={on} onChange={setOn} />
      <Stepper
        label="Ретраи"
        value={n}
        onChange={setN}
        min={0}
        max={2}
        less="Меньше ретраев"
        more="Больше ретраев"
      />
      <Chip pressed={pressed} onToggle={() => setPressed(!pressed)}>
        train
      </Chip>
    </>
  );
}

describe("элементы управления", () => {
  it("сегменты — группа радиокнопок со стрелками", async () => {
    render(<Controlled />);
    const radios = screen.getAllByRole("radio");
    expect(screen.getByRole("radiogroup", { name: "Выбор" })).toBeInTheDocument();
    expect(radios.map((r) => r.getAttribute("tabindex"))).toEqual(["0", "-1", "-1"]);
    await userEvent.click(screen.getByRole("radio", { name: "Второй" }));
    expect(screen.getByRole("radio", { name: "Второй" })).toBeChecked();
    await userEvent.keyboard("{ArrowRight}");
    expect(screen.getByRole("radio", { name: "Третий" })).toBeChecked();
    expect(screen.getByRole("radio", { name: "Третий" })).toHaveFocus();
    await userEvent.keyboard("{ArrowRight}");
    expect(screen.getByRole("radio", { name: "Первый" })).toHaveFocus();
  });

  it("переключатель меняет aria-checked", async () => {
    render(<Controlled />);
    const toggle = screen.getByRole("switch", { name: /Вытеснять/ });
    expect(toggle).not.toBeChecked();
    await userEvent.click(toggle);
    expect(toggle).toBeChecked();
  });

  it("шаги не выходят за пределы", async () => {
    render(<Controlled />);
    const more = screen.getByRole("button", { name: "Больше ретраев" });
    await userEvent.click(more);
    expect(screen.getByRole("group", { name: "Ретраи" })).toHaveTextContent("2");
    expect(more).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Меньше ретраев" }));
    await userEvent.click(screen.getByRole("button", { name: "Меньше ретраев" }));
    expect(screen.getByRole("button", { name: "Меньше ретраев" })).toBeDisabled();
  });

  it("чип — кнопка с нажатым состоянием", async () => {
    render(<Controlled />);
    const chip = screen.getByRole("button", { name: "train" });
    expect(chip).toHaveAttribute("aria-pressed", "false");
    await userEvent.click(chip);
    expect(chip).toHaveAttribute("aria-pressed", "true");
  });
});

describe("отображение данных", () => {
  it("статус — цветом и словом", () => {
    render(<StatusChip status="RUNNING" />);
    expect(screen.getByText("RUNNING")).toHaveAttribute("data-status", "RUNNING");
  });

  it("шкала заполняет долю шагов", () => {
    const { container } = render(<PillMeter share={0.48} />);
    expect(container.querySelectorAll('[data-on="true"]')).toHaveLength(5);
    expect(container.querySelectorAll('[data-on="false"]')).toHaveLength(5);
  });

  it("лента — живая область со строками", () => {
    render(<Feed title="Журнал" items={[{ key: "1", time: "2 ч", type: "START", text: "a на node-A" }]} />);
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
    expect(screen.getByText("Журнал").parentElement).toHaveAttribute("aria-live", "polite");
  });
});
