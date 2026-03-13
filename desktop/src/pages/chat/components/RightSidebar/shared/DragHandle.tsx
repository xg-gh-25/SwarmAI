/**
 * Drag handle grip icon for Radar sidebar draggable items.
 *
 * Renders a vertical grip icon (⋮⋮) that is hidden by default and becomes
 * visible on hover of the parent item row (the parent must apply the Tailwind
 * ``group`` class).  Uses the HTML5 Drag-and-Drop API to transfer a
 * ``DropPayload`` as ``application/json`` via ``dataTransfer.setData``.
 *
 * During a drag the element is made semi-transparent to provide a ghost
 * preview, and opacity is restored on drag-end.
 *
 * Key exports:
 * - ``DragHandle`` — The grip icon component
 */

import { useCallback } from 'react';
import type { DropPayload } from '../types';

/** Props accepted by the DragHandle component. */
interface DragHandleProps {
  /** The typed payload to transfer on drag. */
  payload: DropPayload;
}

export function DragHandle({ payload }: DragHandleProps) {
  const handleDragStart = useCallback(
    (e: React.DragEvent<HTMLSpanElement>) => {
      e.dataTransfer.setData('application/json', JSON.stringify(payload));
      e.dataTransfer.effectAllowed = 'copy';

      // Semi-transparent ghost preview while dragging
      const target = e.currentTarget;
      target.style.opacity = '0.4';
    },
    [payload],
  );

  const handleDragEnd = useCallback(
    (e: React.DragEvent<HTMLSpanElement>) => {
      // Restore full opacity when drag ends
      e.currentTarget.style.opacity = '1';
    },
    [],
  );

  return (
    <span
      draggable="true"
      role="button"
      aria-label="Drag to chat"
      className="opacity-0 group-hover:opacity-100 cursor-grab active:cursor-grabbing select-none text-[var(--color-text-muted)] text-xs leading-none transition-opacity duration-150"
      onDragStart={handleDragStart}
      onDragEnd={handleDragEnd}
    >
      ⋮⋮
    </span>
  );
}
