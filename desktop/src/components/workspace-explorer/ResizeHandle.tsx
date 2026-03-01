import { useCallback, useEffect, useRef, useState } from 'react';
import { LAYOUT_CONSTANTS } from '../../contexts/LayoutContext';

/**
 * ResizeHandle component - vertical bar on the right edge of WorkspaceExplorer
 * 
 * Implements drag-to-resize functionality with mouse events.
 * Enforces min/max width constraints (200px - 500px).
 * 
 * Requirements:
 * - 1.7: Workspace_Explorer SHALL be resizable by dragging its right edge
 * - 11.5: System SHALL enforce minimum and maximum width constraints
 */

interface ResizeHandleProps {
  /** Current width of the workspace explorer */
  currentWidth: number;
  /** Callback when width changes during resize */
  onWidthChange: (width: number) => void;
  /** Whether resizing is disabled (e.g., when collapsed) */
  disabled?: boolean;
}

export default function ResizeHandle({
  currentWidth,
  onWidthChange,
  disabled = false,
}: ResizeHandleProps) {
  const [isDragging, setIsDragging] = useState(false);
  const [isHovered, setIsHovered] = useState(false);
  const startXRef = useRef<number>(0);
  const startWidthRef = useRef<number>(0);

  const handleMouseDown = useCallback((event: React.MouseEvent) => {
    if (disabled) return;
    
    event.preventDefault();
    setIsDragging(true);
    startXRef.current = event.clientX;
    startWidthRef.current = currentWidth;
  }, [disabled, currentWidth]);

  const handleMouseMove = useCallback((event: MouseEvent) => {
    if (!isDragging) return;

    const deltaX = event.clientX - startXRef.current;
    const newWidth = startWidthRef.current + deltaX;
    
    // Clamp to min/max constraints
    const clampedWidth = Math.max(
      LAYOUT_CONSTANTS.MIN_WORKSPACE_EXPLORER_WIDTH,
      Math.min(LAYOUT_CONSTANTS.MAX_WORKSPACE_EXPLORER_WIDTH, newWidth)
    );
    
    onWidthChange(clampedWidth);
  }, [isDragging, onWidthChange]);

  const handleMouseUp = useCallback(() => {
    setIsDragging(false);
  }, []);

  // Add global mouse event listeners when dragging
  useEffect(() => {
    if (isDragging) {
      document.addEventListener('mousemove', handleMouseMove);
      document.addEventListener('mouseup', handleMouseUp);
      // Prevent text selection during drag
      document.body.style.userSelect = 'none';
      document.body.style.cursor = 'col-resize';
    }

    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
      document.body.style.userSelect = '';
      document.body.style.cursor = '';
    };
  }, [isDragging, handleMouseMove, handleMouseUp]);

  if (disabled) {
    return null;
  }

  return (
    <div
      className={`absolute top-0 right-0 w-1 h-full cursor-col-resize transition-colors z-10 ${
        isDragging || isHovered
          ? 'bg-[var(--color-primary)]'
          : 'bg-transparent hover:bg-[var(--color-primary-hover)]'
      }`}
      onMouseDown={handleMouseDown}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      data-testid="resize-handle"
      role="separator"
      aria-orientation="vertical"
      aria-valuenow={currentWidth}
      aria-valuemin={LAYOUT_CONSTANTS.MIN_WORKSPACE_EXPLORER_WIDTH}
      aria-valuemax={LAYOUT_CONSTANTS.MAX_WORKSPACE_EXPLORER_WIDTH}
      aria-label="Resize workspace explorer"
    >
      {/* Wider hit area for easier grabbing */}
      <div 
        className="absolute top-0 -left-1 w-3 h-full"
        aria-hidden="true"
      />
    </div>
  );
}

/**
 * Utility function to clamp width to constraints
 * Exported for use in property tests
 */
export function clampWidth(
  width: number,
  min: number = LAYOUT_CONSTANTS.MIN_WORKSPACE_EXPLORER_WIDTH,
  max: number = LAYOUT_CONSTANTS.MAX_WORKSPACE_EXPLORER_WIDTH
): number {
  return Math.max(min, Math.min(max, width));
}
