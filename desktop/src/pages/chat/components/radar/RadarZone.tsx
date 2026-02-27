/**
 * Reusable collapsible zone wrapper for Swarm Radar.
 *
 * Renders a clickable header with emoji, label, tinted badge count,
 * and expand/collapse animation. Handles empty state display.
 *
 * - ``RadarZone``      — The zone wrapper component
 * - ``RadarZoneProps``  — Props interface
 */

import { type ReactNode } from 'react';
import clsx from 'clsx';
import type { BadgeTint } from './radarIndicators';

export interface RadarZoneProps {
  zoneId: string;
  emoji: string;
  label: string;
  count: number;
  badgeTint: BadgeTint;
  isExpanded: boolean;
  onToggle: () => void;
  children?: ReactNode;
  emptyMessage?: string;
}

export function RadarZone({
  zoneId,
  emoji,
  label,
  count,
  badgeTint,
  isExpanded,
  onToggle,
  children,
  emptyMessage,
}: RadarZoneProps) {
  const contentId = `zone-content-${zoneId}`;

  return (
    <div className="radar-zone">
      <button
        className="radar-zone-header"
        onClick={onToggle}
        aria-expanded={isExpanded}
        aria-controls={contentId}
      >
        <span className="radar-zone-emoji">{emoji}</span>
        <span className="radar-zone-label">{label}</span>
        <span
          className={clsx('radar-zone-badge', `badge-${badgeTint}`)}
          aria-label={`${label}, ${count} items`}
        >
          {count}
        </span>
      </button>

      {isExpanded && (
        <div id={contentId} className="radar-zone-content" role="list">
          {count === 0 && emptyMessage ? (
            <div className="radar-empty-state">{emptyMessage}</div>
          ) : (
            children
          )}
        </div>
      )}
    </div>
  );
}
