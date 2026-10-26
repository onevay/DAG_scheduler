import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { type ReactNode, useState } from "react";
import { describe, expect, it } from "vitest";

import type { Draft } from "../draft/draft";
import { DraftProvider, useDraft } from "../draft/DraftContext";
import { Canvas } from "./Canvas";

const DRAFT: Draft = {
  tasks: [
    { id: "prep", duration: 2, resources: { gpu: 1 } },
    { id: "train", duration: 6, resources: { gpu: 4 }, dependencies: ["prep"], kind: "train" },
  ],
  cluster: { workers: [{ id: "node-A", resources: { gpu: 4 } }] },
  events: [
    {
      time: 3,
      type: "submit_task",
      task: { id: "urgent", duration: 1, dependencies: ["prep"] },
    },
  ],
};

function Ids() {
  const { draft } = useDraft();
  return <output data-testid="ids">{draft.tasks.map((t) => t.id).join(",")}</output>;
}

function Harness({ children }: { children?: ReactNode }) {
  const [selected, setSelected] = useState<string | null>(null);
  return (
    <DraftProvider initial={DRAFT}>
      <Canvas
        selected={selected}
        onSelect={setSelected}
        criticalPath={["prep", "train"]}
        invalid={new Set(["train"])}
        lowerBound={8}
      />
      <Ids />
      <output data-testid="selected">{selected ?? ""}</output>
      {children}
    </DraftProvider>
  );
}

const ids = () => screen.getByTestId("ids").textContent;
// Узлы — по data-testid самого React Flow: в jsdom они не измерены и скрыты от запросов по роли
const node = (id: string) => screen.findByTestId(`rf__node-${id}`);

describe("Canvas", () => {
  it("draws tasks, submitted tasks and the header", async () => {
    render(<Harness />);
    expect(await node("prep")).toHaveAttribute("aria-label", "prep, 2 Ч, 1 GPU");
    expect(await node("urgent")).toHaveAttribute(
      "aria-label",
      "urgent, 1 Ч, без ресурсов, подаётся во время прогона",
    );
    expect(screen.getByText("DAG · 3 ЗАДАЧ")).toBeInTheDocument();
    expect(screen.getByText("8 Ч")).toBeInTheDocument();
    expect((await node("train")).querySelector("[data-invalid]")).toHaveAttribute("data-invalid", "true");
  });

  it("adds a task from the palette and selects it", async () => {
    render(<Harness />);
    await userEvent.click(screen.getByRole("button", { name: "eval" }));
    expect(ids()).toBe("prep,train,task_1");
    expect(screen.getByTestId("selected")).toHaveTextContent("task_1");
    expect(await node("task_1")).toHaveAttribute("aria-label", "task_1, 1 Ч, 1 GPU");
  });

  it("deletes the selected task, but not a submitted one", async () => {
    render(<Harness />);
    const remove = screen.getByRole("button", { name: "Удалить задачу" });
    expect(remove).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "data" }));
    expect(remove).toBeEnabled();
    await userEvent.click(remove);
    expect(ids()).toBe("prep,train");
    expect(screen.getByTestId("selected")).toHaveTextContent("");
  });
});
