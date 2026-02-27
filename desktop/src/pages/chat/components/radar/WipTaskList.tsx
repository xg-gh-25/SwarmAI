/**
 * Sorted list of active WIP task items within the In Progress zone.
 *
 * Exports:
 * - WipTaskList — Renders sorted RadarWipTask items, delegates actions to parent
 */

import type { RadarWipTask } from '../../../../types';
import { WipTaskItem } from './WipTaskItem';

interface WipTaskListProps {
  tasks: RadarWipTask[];
  onViewThread: (taskId: string) => void;
  onCancel: (taskId: string) => void;
}

export function WipTaskList({ tasks, onViewThread, onCancel }: WipTaskListProps) {
  if (tasks.length === 0) return null;

  return (
    <ul role="list" className="radar-wip-list">
      {tasks.map((task) => (
        <WipTaskItem
          key={task.id}
          task={task}
          onViewThread={() => onViewThread(task.id)}
          onCancel={() => onCancel(task.id)}
        />
      ))}
    </ul>
  );
}
