/**
 * Single completed task item with completion timestamp and overflow action menu.
 *
 * Exports:
 * - CompletedTaskItem — Renders one RadarCompletedTask with timestamp and ⋯ menu
 */

import { useState, useEffect, useRef } from 'react';
import type { RadarCompletedTask } from '../../../../types';

interface CompletedTaskItemProps {
  task: RadarCompletedTask;
  onViewThread: () => void;
  onResume: () => void;
}

/** Compute a human-readable relative timestamp. */
function relativeTime(isoDate: string): string {
  const ms = Date.now() - new Date(isoDate).getTime();
  if (ms < 0) return 'just now';
  const mins = Math.floor(ms / 60_000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days === 1) return 'Yesterday';
  return `${days}d ago`;
}

export function CompletedTaskItem({
  task,
  onViewThread,
  onResume,
}: CompletedTaskItemProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  // Close menu on outside click
  useEffect(() => {
    if (!menuOpen) return;
    const handleClick = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [menuOpen]);

  const handleBodyClick = () => {
    if (!menuOpen) onViewThread();
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      setMenuOpen((prev) => !prev);
    } else if (e.key === 'Escape' && menuOpen) {
      setMenuOpen(false);
    }
  };

  return (
    <li
      role="listitem"
      className="radar-completed-item"
      tabIndex={0}
      onKeyDown={handleKeyDown}
    >
      <div className="radar-completed-item-body" onClick={handleBodyClick}>
        <span className="radar-completed-item-title">{task.title}</span>
        <span className="radar-completed-item-meta">
          {relativeTime(task.completedAt)}
          {task.agentId && (
            <span className="radar-completed-item-agent">
              · {task.agentId}
            </span>
          )}
        </span>
        {task.description && (
          <span className="radar-completed-item-summary">
            {task.description}
          </span>
        )}
      </div>

      {/* Overflow action menu */}
      <div className="radar-completed-item-actions" ref={menuRef}>
        <button
          className="radar-completed-overflow-btn"
          onClick={(e) => {
            e.stopPropagation();
            setMenuOpen((prev) => !prev);
          }}
          aria-label={`Actions for ${task.title}`}
        >
          ⋯
        </button>

        {menuOpen && (
          <div className="radar-completed-menu">
            <button
              onClick={() => { setMenuOpen(false); onViewThread(); }}
            >
              View Thread
            </button>
            <button
              onClick={() => { setMenuOpen(false); onResume(); }}
            >
              Resume
            </button>
          </div>
        )}
      </div>
    </li>
  );
}
