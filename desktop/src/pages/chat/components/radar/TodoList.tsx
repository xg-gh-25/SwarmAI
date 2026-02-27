/**
 * Sorted list of active ToDo items within the Needs Attention zone.
 *
 * Receives pre-sorted, pre-filtered data from ``useTodoZone`` — no sorting
 * logic here. Renders nothing when the list is empty (parent RadarZone
 * handles the empty state message).
 *
 * Exports:
 * - ``TodoList``      — Renders sorted RadarTodo items, delegates actions to parent
 * - ``TodoListProps``  — Props interface
 */

import type { RadarTodo } from '../../../../types';
import { TodoItem } from './TodoItem';

export interface TodoListProps {
  todos: RadarTodo[];
  onStart: (todoId: string) => void;
  onEdit: (todoId: string) => void;
  onComplete: (todoId: string) => void;
  onCancel: (todoId: string) => void;
  onDelete: (todoId: string) => void;
}

export function TodoList({
  todos,
  onStart,
  onEdit,
  onComplete,
  onCancel,
  onDelete,
}: TodoListProps) {
  if (todos.length === 0) return null;

  return (
    <ul role="list" className="radar-todo-list">
      {todos.map((todo) => (
        <TodoItem
          key={todo.id}
          todo={todo}
          onStart={() => onStart(todo.id)}
          onEdit={() => onEdit(todo.id)}
          onComplete={() => onComplete(todo.id)}
          onCancel={() => onCancel(todo.id)}
          onDelete={() => onDelete(todo.id)}
        />
      ))}
    </ul>
  );
}
