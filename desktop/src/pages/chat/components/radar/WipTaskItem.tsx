/**
 * Single WIP task item with status indicator, elapsed time, and overflow action menu.
 *
 * Exports:
 * - WipTaskItem — Renders one RadarWipTask with status, elapsed time, and ⋯ menu
 */

import { useState, useEffect, useRef } from 'react';
import clsx from 'clsx';
import type { RadarWipTask } from '../../../../types';

interface WipTaskItemProps {
  task: RadarWipTask;
  onViewThread: () => void;
  onCancel: () => void;
}

/** Map task status to its emoji indicator. */
function statusIndicator(status: string): string {
  switch (status) {
    case 'wip': return '🔄';
    case 'draft': return '📋';
    case 'blocked': return '🚫';
    default: return '🔄';
  }
}

/** Compute elapsed time string from a start ISO timestamp. */
function elapsedTime(startedAt: string | null): string {
  if (!startedAt) return '';
  const ms = Date.now() - new Date(startedAt).getTime();
  if (ms < 0) return '';
  const mins = Math.floor(ms / 60_000);
  if (mins < 60) return `${mins}m`;
  const hrs = Math.floor(mins / 60);
  const rem = mins % 60;
  return rem > 0 ? `${hrs}h ${rem}m` : `${hrs}h`;
}

export function WipTaskItem({ task, onViewThread, onCancel }: WipTaskItemProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [confirmAction, setConfirmAction] = useState<'cancel' | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  // Close menu on outside click
  useEffect(() => {
    if (!menuOpen) return;
    const handleClick = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
        setConfirmAction(null);
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
      setConfirmAction(null);
    }
  };

  const handleConfirmCancel = () => {
    setMenuOpen(false);
    setConfirmAction(null);
    onCancel();
  };

  return (
    <li
      role="listitem"
      className={clsx(
        'radar-wip-item',
        task.status === 'blocked' && 'radar-wip-item--blocked',
      )}
      tabIndex={0}
      onKeyDown={handleKeyDown}
    >
      <div className="radar-wip-item-body" onClick={handleBodyClick}>
        <span className="radar-wip-item-status">
          {statusIndicator(task.status)}
        </span>
        <span className="radar-wip-item-title">{task.title}</span>
        {task.hasWaitingInput && (
          <span className="radar-wip-item-waiting">⏳</span>
        )}
        {task.startedAt && (
          <span className="radar-wip-item-elapsed">
            {elapsedTime(task.startedAt)}
          </span>
        )}
      </div>

      {/* Overflow action menu */}
      <div className="radar-wip-item-actions" ref={menuRef}>
        <button
          className="radar-wip-overflow-btn"
          onClick={(e) => {
            e.stopPropagation();
            setMenuOpen((prev) => !prev);
            setConfirmAction(null);
          }}
          aria-label={`Actions for ${task.title}`}
        >
          ⋯
        </button>

        {menuOpen && !confirmAction && (
          <div className="radar-wip-menu">
            <button onClick={() => { setMenuOpen(false); onViewThread(); }}>
              View Thread
            </button>
            <button onClick={() => setConfirmAction('cancel')}>
              Cancel
            </button>
          </div>
        )}

        {menuOpen && confirmAction === 'cancel' && (
          <div className="radar-wip-menu radar-wip-menu--confirm">
            <span className="radar-confirm-text">Cancel this task?</span>
            <button
              className="radar-confirm-btn"
              onClick={handleConfirmCancel}
            >
              Confirm
            </button>
            <button
              className="radar-confirm-back-btn"
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
