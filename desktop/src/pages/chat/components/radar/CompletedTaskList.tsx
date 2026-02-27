/**
 * Sorted list of recently completed task items within the Completed zone.
 *
 * Exports:
 * - CompletedTaskList — Renders sorted RadarCompletedTask items, delegates actions to parent
 */

import type { RadarCompletedTask } from '../../../../types';
import { CompletedTaskItem } from './CompletedTaskItem';

interface CompletedTaskListProps {
  tasks: RadarCompletedTask[];
  onViewThread: (taskId: string) => void;
  onResume: (taskId: string) => void;
}

export function CompletedTaskList({
  tasks,
  onViewThread,
  onResume,
}: CompletedTaskListProps) {
  if (tasks.length === 0) return null;

  return (
    <ul role="list" className="radar-completed-list">
      {tasks.map((task) => (
        <CompletedTaskItem
          key={task.id}
          task={task}
          onViewThread={() => onViewThread(task.id)}
          onResume={() => onResume(task.id)}
        />
      ))}
    </ul>
  );
}
