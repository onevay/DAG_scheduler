import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import type { Draft } from "../draft/draft";
import { DraftProvider, useDraft } from "../draft/DraftContext";
import { parseConstraints, priorityClass, TaskPanel } from "./TaskPanel";

const DRAFT: Draft = {
  tasks: [
    { id: "prep", duration: 2, resources: { gpu: 1 } },
    { id: "train", duration: 6, resources: { gpu: 4 }, dependencies: ["prep"] },
  ],
  cluster: {
    workers: [
      { id: "a", resources: { gpu: 4 } },
      { id: "b", resources: { gpu: 2 } },
    ],
  },
  events: [{ time: 5, type: "submit_task", task: { id: "urgent", duration: 1 } }],
};

let latest: Draft = DRAFT;
function Spy() {
  latest = useDraft().draft;
  return null;
}

function Harness({ start }: { start: string }) {
  const [selected, setSelected] = useState<string | null>(start);
  return (
    <DraftProvider initial={DRAFT}>
      <TaskPanel selected={selected} onSelect={setSelected} />
      <Spy />
    </DraftProvider>
  );
}

const task = (id: string) => latest.tasks.find((t) => t.id === id);

describe("TaskPanel", () => {
  it("edits fields of the selected task", async () => {
    render(<Harness start="train" />);
    expect(screen.getByText("помещается на 1 из 2 узлов")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Дольше" }));
    await userEvent.click(screen.getByRole("button", { name: "eval" }));
    await userEvent.click(screen.getByRole("switch", { name: /Можно вытеснить/ }));
    await userEvent.type(screen.getByLabelText("Дедлайн, ч"), "12");
    await userEvent.click(screen.getByRole("button", { name: "Убрать зависимость от prep" }));
    expect(task("train")).toMatchObject({
      duration: 7,
      kind: "eval",
      preemptible: true,
      deadline: 12,
      dependencies: [],
    });
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Добавить зависимость" }), "prep");
    expect(task("train")?.dependencies).toEqual(["prep"]);
  });

  it("renames on Enter and refuses a taken id", async () => {
    render(<Harness start="prep" />);
    const id = screen.getByLabelText("Задача");
    await userEvent.clear(id);
    await userEvent.type(id, "train{Enter}");
    expect(task("prep")).toBeDefined();
    expect(id).toHaveValue("prep");
    await userEvent.clear(id);
    await userEvent.type(id, "load{Enter}");
    expect(task("load")).toBeDefined();
    expect(task("train")?.dependencies).toEqual(["load"]);
    expect(screen.getByLabelText("Задача")).toHaveValue("load");
  });

  it("edits a submitted task inside its event", async () => {
    render(<Harness start="urgent" />);
    await userEvent.click(screen.getByRole("button", { name: "Дольше" }));
    const time = screen.getByLabelText("Подаётся в момент, ч");
    await userEvent.clear(time);
    await userEvent.type(time, "8");
    expect(latest.events?.[0]).toMatchObject({ time: 8, task: { id: "urgent", duration: 2 } });
  });

  it("asks to pick a task when nothing is selected", () => {
    render(<Harness start="" />);
    expect(screen.getByText(/Выберите задачу на холсте/)).toBeInTheDocument();
  });
});

describe("helpers", () => {
  it("parses label constraints and names priority classes", () => {
    expect(parseConstraints("gpu=a100, zone = eu, junk, =x")).toEqual({ gpu: "a100", zone: "eu" });
    expect([0, 1, 2, 5, 8, 10].map(priorityClass)).toEqual([
      "low",
      "low",
      "normal",
      "high",
      "critical",
      "critical",
    ]);
  });
});
