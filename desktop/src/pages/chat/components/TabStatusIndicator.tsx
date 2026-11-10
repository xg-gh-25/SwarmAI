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
import { useTranslation } from 'react-i18next';
import { tf } from '../tf';

interface TabStatusIndicatorProps {
  status: TabStatus;
}

/** Glyph colour: the status colour mixed 60/40 with --color-text, so the 12px glyph
 *  is >= 4.5:1 on the tab in both themes (the plain warning colour is 2.8:1 on the
 *  light chrome). Dots use the plain theme colour. Theme variables only (index.css). */
const ERROR_TEXT = 'text-[color-mix(in_srgb,var(--color-status-error)_60%,var(--color-text))]';
const WARNING_TEXT = 'text-[color-mix(in_srgb,var(--color-status-warning)_60%,var(--color-text))]';

/**
 * Renders a small visual indicator based on the tab's lifecycle status.
 * Returns null for 'idle' tabs — no indicator shown.
 */
export function TabStatusIndicator({ status }: TabStatusIndicatorProps) {
  const { t } = useTranslation();
  switch (status) {
    case 'streaming':
      return (
        <span
          className="w-2 h-2 rounded-full bg-[var(--color-primary)] animate-pulse inline-block"
          role="img"
          aria-label={tf(t, 'chat.tabStatus.streaming', 'Streaming')}
        />
      );
    case 'waiting_input':
      return (
        <span
          className={`${WARNING_TEXT} text-small font-semibold leading-none`}
          role="img"
          aria-label={tf(t, 'chat.tabStatus.waitingInput', 'Waiting for input')}
        >
          ?
        </span>
      );
    case 'permission_needed':
      return (
        <span
          className={`${WARNING_TEXT} text-small leading-none`}
          role="img"
          aria-label={tf(t, 'chat.tabStatus.permissionNeeded', 'Permission needed')}
        >
          ⚠
        </span>
      );
    case 'error':
      return (
        <span
          className={`${ERROR_TEXT} text-small font-semibold leading-none`}
          role="img"
          aria-label={tf(t, 'chat.tabStatus.error', 'Error')}
        >
          !
        </span>
      );
    case 'complete_unread':
      return (
        <span
          className="w-2 h-2 rounded-full bg-[var(--color-status-success)] inline-block"
          role="img"
          aria-label={tf(t, 'chat.tabStatus.newContent', 'New content')}
        />
      );
    default:
      return null;
  }
}
