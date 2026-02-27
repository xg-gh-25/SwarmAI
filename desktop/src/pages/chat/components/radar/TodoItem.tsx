/**
 * Single ToDo item with indicators, metadata, and overflow action menu.
 *
 * Renders one RadarTodo within the Needs Attention zone. Displays:
 * - Title (truncated to 1 line via CSS)
 * - Priority indicator emoji via ``getPriorityIndicator``
 * - Timeline indicator emoji via ``getTimelineIndicator``
 * - Source type label via ``getSourceTypeLabel``
 * - Formatted due date
 *
 * The ⋯ overflow button appears on hover and opens a positioned menu
 * with Start, Edit, Complete, Cancel, Delete actions. Cancel and Delete
 * trigger inline confirmation before executing.
 *
 * Exports:
 * - ``TodoItem``      — The single ToDo item component
 * - ``TodoItemProps``  — Props interface
 */

import { useState, useEffect, useRef, useCallback } from 'react';
import clsx from 'clsx';
import type { RadarTodo } from '../../../../types';
import {
  getPriorityIndicator,
  getTimelineIndicator,
  getSourceTypeLabel,
} from './radarIndicators';

export interface TodoItemProps {
  todo: RadarTodo;
  onStart: () => void;
  onEdit: () => void;
  onComplete: () => void;
  onCancel: () => void;
  onDelete: () => void;
}


/** Format a due date string for display. */
function formatDueDate(dueDate: string | null): string {
  if (!dueDate) return '';
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const due = new Date(dueDate);
  due.setHours(0, 0, 0, 0);
  const diffMs = due.getTime() - today.getTime();
  const diffDays = Math.round(diffMs / (1000 * 60 * 60 * 24));
  if (diffDays === 0) return 'Due today';
  if (diffDays === 1) return 'Due tomorrow';
  if (diffDays === -1) return 'Overdue 1d';
  if (diffDays < 0) return `Overdue ${Math.abs(diffDays)}d`;
  return `Due in ${diffDays}d`;
}

export function TodoItem({
  todo,
  onStart,
  onEdit,
  onComplete,
  onCancel,
  onDelete,
}: TodoItemProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [confirmAction, setConfirmAction] = useState<
    'cancel' | 'delete' | null
  >(null);
  const menuRef = useRef<HTMLDivElement>(null);

  const closeMenu = useCallback(() => {
    setMenuOpen(false);
    setConfirmAction(null);
  }, []);

  // Close menu on outside click
  useEffect(() => {
    if (!menuOpen) return;
    const handleClick = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        closeMenu();
      }
    };
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [menuOpen, closeMenu]);

  // Close menu on Escape key
  useEffect(() => {
    if (!menuOpen) return;
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') closeMenu();
    };
    document.addEventListener('keydown', handleKey);
    return () => document.removeEventListener('keydown', handleKey);
  }, [menuOpen, closeMenu]);

  const priorityIcon = getPriorityIndicator(todo.priority);
  const timelineIcon = getTimelineIndicator(todo.status, todo.dueDate);
  const sourceLabel = getSourceTypeLabel(todo.sourceType);
  const dueDateText = formatDueDate(todo.dueDate);

  const handleAction = (action: () => void) => {
    action();
    closeMenu();
  };

  return (
    <li
      role="listitem"
      className={clsx(
        'radar-todo-item',
        todo.status === 'overdue' && 'radar-todo-item--overdue',
      )}
    >
      <div className="radar-todo-item-content">
        <div className="radar-todo-item-indicators">
          {priorityIcon && <span className="radar-todo-indicator">{priorityIcon}</span>}
          {timelineIcon && <span className="radar-todo-indicator">{timelineIcon}</span>}
        </div>
        <span className="radar-todo-item-title">{todo.title}</span>
        <div className="radar-todo-item-meta">
          {sourceLabel && <span className="radar-todo-source">{sourceLabel}</span>}
          {dueDateText && <span className="radar-todo-due">{dueDateText}</span>}
        </div>
      </div>

      {/* Overflow menu trigger */}
      <div className="radar-todo-item-actions" ref={menuRef}>
        <button
          className="radar-todo-overflow-btn"
          onClick={() => setMenuOpen((prev) => !prev)}
          aria-label={`Actions for ${todo.title}`}
        >
          ⋯
        </button>

        {menuOpen && !confirmAction && (
          <div className="radar-todo-menu">
            <button onClick={() => handleAction(onStart)}>Start</button>
            <button onClick={() => handleAction(onEdit)}>Edit</button>
            <button onClick={() => handleAction(onComplete)}>Complete</button>
            <button onClick={() => setConfirmAction('cancel')}>Cancel</button>
            <button onClick={() => setConfirmAction('delete')}>Delete</button>
          </div>
        )}

        {menuOpen && confirmAction && (
          <div className="radar-todo-menu radar-todo-menu--confirm">
            <span className="radar-todo-confirm-text">
              {confirmAction === 'cancel'
                ? 'Cancel this ToDo?'
                : 'Delete this ToDo?'}
            </span>
            <button
              className="radar-todo-confirm-btn"
              onClick={() =>
                handleAction(confirmAction === 'cancel' ? onCancel : onDelete)
              }
            >
              Confirm
            </button>
            <button
              className="radar-todo-back-btn"
              onClick={() => setConfirmAction(null)}
            >
              Back
            </button>
          </div>
        )}
      </div>
    </li>
  );
}
