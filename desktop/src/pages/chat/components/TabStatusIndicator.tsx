/**
 * Visual status indicator for chat tab headers.
 *
 * Renders a small icon/dot in the tab header reflecting the tab's lifecycle
 * state (streaming, waiting for input, permission needed, error, or unread
 * completion). Returns ``null`` for idle tabs (no indicator).
 *
 * - ``TabStatusIndicator`` — The indicator component
 *
 * Accessibility: Each indicator includes an ``aria-label`` for screen readers.
 * The pulsing animation respects ``prefers-reduced-motion`` via Tailwind defaults.
 *
 * @module TabStatusIndicator
 */
import type { TabStatus } from '../../../hooks/useUnifiedTabState';

interface TabStatusIndicatorProps {
  status: TabStatus;
}

/**
 * Renders a small visual indicator based on the tab's lifecycle status.
 * Returns null for 'idle' tabs — no indicator shown.
 */
export function TabStatusIndicator({ status }: TabStatusIndicatorProps) {
  switch (status) {
    case 'streaming':
      return (
        <span
          className="w-2 h-2 rounded-full bg-blue-500 animate-pulse inline-block"
          role="img"
          aria-label="Streaming"
        />
      );
    case 'waiting_input':
      return (
        <span
          className="text-orange-500 text-xs font-bold leading-none"
          role="img"
          aria-label="Waiting for input"
        >
          ?
        </span>
      );
    case 'permission_needed':
      return (
        <span
          className="text-yellow-500 text-xs leading-none"
          role="img"
          aria-label="Permission needed"
        >
          ⚠
        </span>
      );
    case 'error':
      return (
        <span
          className="text-red-500 text-xs font-bold leading-none"
          role="img"
          aria-label="Error"
        >
          !
        </span>
      );
    case 'complete_unread':
      return (
        <span
          className="w-2 h-2 rounded-full bg-green-500 inline-block"
          role="img"
          aria-label="New content"
        />
      );
    default:
      return null;
  }
}
