import "@xyflow/react/dist/base.css";

import {
  applyNodeChanges,
  type Connection,
  type Edge,
  type EdgeChange,
  type NodeChange,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
} from "@xyflow/react";
import { type DragEvent, useEffect, useMemo, useRef, useState } from "react";

import type { TaskKind } from "../api/types";
import { freeId } from "../draft/draft";
import { useDraft } from "../draft/DraftContext";
import { Icon, type IconName } from "../shell/Icon";
import styles from "./Canvas.module.css";
import { graphEdges, graphTasks, layeredLayout, type XY } from "./graph";
import { type TaskFlowNode, TaskNode } from "./TaskNode";
import { formatResources } from "../ui/resources";

const NODE_TYPES = { task: TaskNode };
const KINDS: readonly TaskKind[] = ["data", "train", "eval", "infer", "other"];
const UNITS = { hour: "Ч", minute: "МИН", second: "С" } as const;
const DRAG_TYPE = "application/x-expsched-kind";

// Подсказки React Flow для читалок экрана — по-русски, как весь интерфейс
const ARIA_LABELS = {
  "node.a11yDescription.default": "Enter или пробел — выбрать задачу, Delete — удалить, Escape — отменить.",
  "node.a11yDescription.keyboardDisabled":
    "Enter или пробел — выбрать задачу, стрелки — передвинуть, Delete — удалить, Escape — отменить.",
  "node.a11yDescription.ariaLiveMessage": ({ x, y }: { direction: string; x: number; y: number }) =>
    `Задача передвинута: x ${x}, y ${y}`,
  "edge.a11yDescription.default": "Enter или пробел — выбрать связь, Delete — удалить, Escape — отменить.",
  "handle.ariaLabel": "Точка связи",
};

export interface CanvasProps {
  selected: string | null;
  onSelect: (id: string | null) => void;
  criticalPath: readonly string[];
  invalid: ReadonlySet<string>;
  lowerBound?: number;
}

/** Граф задач черновика: добавление из палитры, связи перетаскиванием, удаление, выбор. */
export function Canvas(props: CanvasProps) {
  return (
    <ReactFlowProvider>
      <Board {...props} />
    </ReactFlowProvider>
  );
}

function Board({ selected, onSelect, criticalPath, invalid, lowerBound }: CanvasProps) {
  const { draft, dispatch } = useDraft();
  const flow = useReactFlow();
  const tasks = useMemo(() => graphTasks(draft), [draft]);
  const layout = useMemo(() => layeredLayout(tasks.map((t) => t.task)), [tasks]);
  const unit = UNITS[draft.time_unit ?? "hour"];
  // Места, куда задачи бросили из палитры: важнее раскладки, пока узел не появился на холсте
  const dropped = useRef(new Map<string, XY>());
  const [nodes, setNodes] = useState<TaskFlowNode[]>([]);
  const [selectedEdge, setSelectedEdge] = useState<string | null>(null);

  // Узлы живут в состоянии холста: так сохраняются положение после перетаскивания и размеры, измеренные React Flow
  useEffect(() => {
    setNodes((prev) => {
      const before = new Map(prev.map((n) => [n.id, n]));
      return tasks.map(({ task, submitted }): TaskFlowNode => {
        const old = before.get(task.id);
        return {
          ...old,
          id: task.id,
          type: "task",
          position: old?.position ?? dropped.current.get(task.id) ?? layout.get(task.id) ?? { x: 0, y: 0 },
          selected: task.id === selected,
          deletable: !submitted,
          ariaLabel: `${task.id}, ${task.duration} ${unit}, ${formatResources(task.resources)}${submitted ? ", подаётся во время прогона" : ""}`,
          data: { task, submitted, invalid: invalid.has(task.id), unit },
        };
      });
    });
  }, [tasks, layout, selected, invalid, unit]);

  const edges = useMemo(
    (): Edge[] =>
      graphEdges(tasks, criticalPath).map((e) => ({
        id: e.id,
        source: e.from,
        target: e.to,
        ariaLabel: `Связь ${e.from} → ${e.to}${e.critical ? ", критический путь" : ""}`,
        selected: e.id === selectedEdge,
        className: e.critical ? styles.critical : e.submitted ? styles.submittedEdge : undefined,
      })),
    [tasks, criticalPath, selectedEdge],
  );

  const onNodesChange = (changes: NodeChange<TaskFlowNode>[]) => {
    for (const c of changes) {
      if (c.type !== "remove") continue;
      dispatch({ type: "removeTask", id: c.id });
      if (c.id === selected) onSelect(null);
    }
    const picked = changes.find((c) => c.type === "select" && c.selected);
    if (picked && "id" in picked) onSelect(picked.id);
    else if (changes.some((c) => c.type === "select" && c.id === selected)) onSelect(null);
    setNodes((nds) =>
      applyNodeChanges(
        changes.filter((c) => c.type !== "remove" && c.type !== "select"),
        nds,
      ),
    );
  };

  const onEdgesChange = (changes: EdgeChange[]) => {
    for (const c of changes) {
      if (c.type === "select") setSelectedEdge(c.selected ? c.id : null);
      if (c.type === "remove") {
        const [from, to] = c.id.split("->");
        if (from && to) dispatch({ type: "unlink", from, to });
      }
    }
  };

  const onConnect = ({ source, target }: Connection) => dispatch({ type: "link", from: source, to: target });

  const add = (kind: TaskKind, at?: XY) => {
    const id = freeId(
      "task",
      tasks.map((t) => t.task.id),
    );
    if (at) dropped.current.set(id, at);
    dispatch({ type: "addTask", task: { id, kind } });
    onSelect(id);
  };

  const onDrop = (event: DragEvent) => {
    const kind = event.dataTransfer.getData(DRAG_TYPE) as TaskKind;
    if (!KINDS.includes(kind)) return;
    event.preventDefault();
    add(kind, flow.screenToFlowPosition({ x: event.clientX, y: event.clientY }));
  };

  const relayout = () => {
    dropped.current.clear();
    setNodes((nds) => nds.map((n) => ({ ...n, position: layout.get(n.id) ?? n.position })));
    requestAnimationFrame(() => void flow.fitView({ duration: 300 }));
  };

  const selectedTask = tasks.find((t) => t.task.id === selected);
  return (
    <>
      <div className={styles.palette} aria-label="Добавить задачу" role="group">
        <span className={styles.paletteHint}>Нажмите или перетащите на холст</span>
        {KINDS.map((kind) => (
          <button
            key={kind}
            type="button"
            draggable
            className={styles.paletteItem}
            onClick={() => add(kind)}
            onDragStart={(e) => e.dataTransfer.setData(DRAG_TYPE, kind)}
          >
            <span className={styles.swatch} style={{ background: `var(--kind-${kind})` }} />
            {kind}
          </button>
        ))}
      </div>

      <section aria-label="Граф задач" className={styles.board}>
        <div className={styles.head}>
          <div className={styles.headText}>
            <span className={styles.tech}>DAG · {tasks.length} ЗАДАЧ</span>
            <span className={styles.caption}>
              лайм — критический путь, пунктир — задача подаётся во время прогона
            </span>
          </div>
          {lowerBound !== undefined && (
            <div className={`${styles.headText} ${styles.right}`}>
              <span className={styles.tech}>
                {lowerBound} {unit}
              </span>
              <span className={styles.caption}>нижняя граница makespan</span>
            </div>
          )}
        </div>
        <div className={styles.flow} onDragOver={(e) => e.preventDefault()} onDrop={onDrop}>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={NODE_TYPES}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            isValidConnection={(c) => c.source !== c.target}
            onPaneClick={() => onSelect(null)}
            deleteKeyCode={["Delete", "Backspace"]}
            ariaLabelConfig={ARIA_LABELS}
            colorMode="dark"
            minZoom={0.3}
            fitView
          />
        </div>
        <div className={styles.tools}>
          <Tool icon="plus" label="Приблизить" onClick={() => void flow.zoomIn({ duration: 200 })} />
          <Tool icon="minus" label="Отдалить" onClick={() => void flow.zoomOut({ duration: 200 })} />
          <Tool icon="fit" label="Показать всё" onClick={() => void flow.fitView({ duration: 300 })} />
          <button type="button" className={styles.tool} onClick={relayout}>
            Разложить по слоям
          </button>
          <button
            type="button"
            className={styles.tool}
            disabled={!selectedTask || selectedTask.submitted}
            onClick={() => {
              if (selected) dispatch({ type: "removeTask", id: selected });
              onSelect(null);
            }}
          >
            Удалить задачу
          </button>
          <span className={styles.caption}>Связь — от правой точки задачи к левой точке зависимой</span>
        </div>
      </section>
    </>
  );
}

function Tool({ icon, label, onClick }: { icon: IconName; label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      className={`${styles.tool} ${styles.round}`}
      aria-label={label}
      title={label}
      onClick={onClick}
    >
      <Icon name={icon} size={16} />
    </button>
  );
}
