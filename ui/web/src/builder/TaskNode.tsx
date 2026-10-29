import { Handle, type Node, type NodeProps, Position } from "@xyflow/react";

import type { Task } from "../api/types";
import styles from "./Canvas.module.css";
import { formatResources } from "../ui/resources";

export type TaskNodeData = {
  task: Task;
  submitted: boolean;
  invalid: boolean;
  unit: string;
};

export type TaskFlowNode = Node<TaskNodeData, "task">;

/** Задача на «чертеже»: id, тип цветной точкой, длительность и ресурсы; слева вход зависимостей, справа выход. */
export function TaskNode({ data, selected }: NodeProps<TaskFlowNode>) {
  const { task, submitted, invalid, unit } = data;
  return (
    <div className={styles.node} data-selected={selected} data-submitted={submitted} data-invalid={invalid}>
      <Handle type="target" position={Position.Left} className={styles.handle} />
      <span className={styles.nodeHead}>
        <span className={styles.nodeId}>{task.id}</span>
        <span className={styles.kind} style={{ background: `var(--kind-${task.kind ?? "other"})` }} />
      </span>
      <span className={styles.nodeMeta}>
        {task.duration} {unit} · {formatResources(task.resources)}
      </span>
      <Handle type="source" position={Position.Right} className={styles.handle} />
    </div>
  );
}
