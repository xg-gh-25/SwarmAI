/**
 * FileEditorPanel — Resizable right-side panel that hosts FileEditorCore.
 *
 * Mounted as a flex sibling to MainChatPanel inside ThreeColumnLayout.
 * Provides drag-to-resize via a vertical handle, width persistence to
 * localStorage, and a mode-toggle to pop out into FileEditorModal.
 *
 * Key exports:
 * - `FileEditorPanel` (default) — Panel wrapper component
 * - `PANEL_CONSTANTS`           — Min/max/default width values
 */

import { useState, useCallback, useEffect, useRef } from 'react';
import FileEditorCore from './FileEditorCore';
import type { FileEditorCoreProps } from './FileEditorCore';

export const PANEL_CONSTANTS = {
  DEFAULT_WIDTH: 500,
  MIN_WIDTH: 320,
  MAX_WIDTH: 1200,
  STORAGE_KEY: 'fileEditorPanelWidth',
} as const;

function getStoredWidth(): number {
  if (typeof window === 'undefined') return PANEL_CONSTANTS.DEFAULT_WIDTH;
  const stored = localStorage.getItem(PANEL_CONSTANTS.STORAGE_KEY);
  if (!stored) return PANEL_CONSTANTS.DEFAULT_WIDTH;
  const parsed = parseInt(stored, 10);
  if (isNaN(parsed)) return PANEL_CONSTANTS.DEFAULT_WIDTH;
  return Math.max(PANEL_CONSTANTS.MIN_WIDTH, Math.min(PANEL_CONSTANTS.MAX_WIDTH, parsed));
}

interface FileEditorPanelProps extends Omit<FileEditorCoreProps, 'variant'> {
  /** Callback to switch to modal mode (pop out). */
  onToggleMode?: () => void;
}

export default function FileEditorPanel(props: FileEditorPanelProps) {
  const [width, setWidth] = useState(getStoredWidth);
  const [isDragging, setIsDragging] = useState(false);
  const startXRef = useRef(0);
  const startWidthRef = useRef(0);

  // Persist width changes
  const updateWidth = useCallback((newWidth: number) => {
    const clamped = Math.max(PANEL_CONSTANTS.MIN_WIDTH, Math.min(PANEL_CONSTANTS.MAX_WIDTH, newWidth));
    setWidth(clamped);
    localStorage.setItem(PANEL_CONSTANTS.STORAGE_KEY, String(clamped));
  }, []);

  // Resize drag handlers
  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    setIsDragging(true);
    startXRef.current = e.clientX;
    startWidthRef.current = width;
  }, [width]);

  useEffect(() => {
    if (!isDragging) return;

    const handleMouseMove = (e: MouseEvent) => {
      // Dragging the left edge of the panel: moving left = wider, right = narrower
      const delta = startXRef.current - e.clientX;
      updateWidth(startWidthRef.current + delta);
    };

    const handleMouseUp = () => {
      setIsDragging(false);
    };

    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
    document.body.style.userSelect = 'none';
    document.body.style.cursor = 'col-resize';

    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
      document.body.style.userSelect = '';
      document.body.style.cursor = '';
    };
  }, [isDragging, updateWidth]);

  return (
    <div
      className="relative flex-shrink-0 flex"
      style={{ width }}
      data-testid="file-editor-panel"
    >
      {/* Resize handle — left edge */}
      <div
        className={`w-1 cursor-col-resize transition-colors flex-shrink-0 ${
          isDragging
            ? 'bg-[var(--color-primary)]'
            : 'bg-[var(--color-border)] hover:bg-[var(--color-primary)]/50'
        }`}
        onMouseDown={handleMouseDown}
        role="separator"
        aria-orientation="vertical"
        aria-valuenow={width}
        aria-valuemin={PANEL_CONSTANTS.MIN_WIDTH}
        aria-valuemax={PANEL_CONSTANTS.MAX_WIDTH}
        aria-label="Resize file editor panel"
        data-testid="panel-resize-handle"
      >
        {/* Wider hit area */}
        <div className="absolute top-0 -left-1 w-3 h-full" aria-hidden="true" />
      </div>

      {/* Editor surface */}
      <div className="flex-1 min-w-0 overflow-hidden">
        <FileEditorCore
          {...props}
          variant="panel"
        />
      </div>
    </div>
  );
}
