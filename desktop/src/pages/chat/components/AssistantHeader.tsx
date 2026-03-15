/**
 * AssistantHeader — branded header line for assistant messages.
 *
 * Renders a single-line header containing:
 * - 🐝 bee emoji (with pulse animation while streaming)
 * - "SwarmAI" label
 * - `·` separator
 * - Formatted timestamp (HH:MM AM/PM)
 *
 * The bee emoji is intentional per Design Decision #5 — assistant messages
 * use the lightweight emoji while the WelcomeScreen uses the brand icon image.
 *
 * @exports AssistantHeader       — The header React component
 * @exports AssistantHeaderProps  — Props interface
 *
 * Validates: Requirements 2.1, 2.2, 2.3, 2.4, 9.2
 */

import React from 'react';
import './swarm-animations.css';

export interface AssistantHeaderProps {
  /** ISO timestamp string for the message */
  timestamp: string;
  /** Whether the assistant message is currently streaming */
  isStreaming?: boolean;
}

/**
 * Formats a timestamp string into a localized HH:MM AM/PM display.
 * Returns an empty string for invalid or missing timestamps.
 */
function formatTimestamp(timestamp: string): string {
  const date = new Date(timestamp);
  return isNaN(date.getTime())
    ? ''
    : date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
}

export const AssistantHeader: React.FC<AssistantHeaderProps> = ({
  timestamp,
  isStreaming = false,
}) => {
  const formattedTime = formatTimestamp(timestamp);

  return (
    <div className="flex items-center gap-1.5 text-[10px] text-[var(--color-text-dim)] mb-0.5">
      <span className="text-[11.5px] font-semibold text-[var(--color-text)]" style={{ letterSpacing: '-0.02em' }}>
        Swarm
      </span>
      {isStreaming && (
        <span className="w-1.5 h-1.5 rounded-full bg-blue-500 animate-pulse inline-block" aria-label="Streaming" />
      )}
      {formattedTime && (
        <>
          <span aria-hidden="true">·</span>
          <span>{formattedTime}</span>
        </>
      )}
    </div>
  );
};

export default AssistantHeader;
