/**
 * Evolution event message component for the chat stream.
 *
 * Renders self-evolution SSE events as distinct, styled chat messages
 * with trigger-type icons, colored borders, and expandable details.
 *
 * Key exports:
 * - ``EvolutionMessage`` — React component for rendering evolution events
 */
import React, { useState } from 'react';
import type { EvolutionEventType } from '../../services/evolution';

export interface EvolutionMessageProps {
  eventType: EvolutionEventType;
  data: Record<string, unknown>;
}

const TRIGGER_STYLES: Record<string, { icon: string; color: string; label: string }> = {
  reactive: { icon: '⚡', color: '#f59e0b', label: 'Reactive' },
  proactive: { icon: '🔍', color: '#3b82f6', label: 'Proactive' },
  stuck: { icon: '🔄', color: '#ef4444', label: 'Stuck' },
};

/**
 * Derive the trigger style from event type and data.
 * For evolution_start/evolution_help_request, uses data.triggerType.
 * For evolution_stuck_detected, always uses 'stuck'.
 * For evolution_result, falls back to 'reactive'.
 */
function getTriggerStyle(eventType: EvolutionEventType, data: Record<string, unknown>) {
  if (eventType === 'evolution_stuck_detected') {
    return TRIGGER_STYLES.stuck;
  }
  const triggerType = (data.triggerType as string) || 'reactive';
  return TRIGGER_STYLES[triggerType] || TRIGGER_STYLES.reactive;
}

/** Build a human-readable summary line for the event. */
function getSummary(
  eventType: EvolutionEventType,
  data: Record<string, unknown>,
  style: { label: string },
): string {
  switch (eventType) {
    case 'evolution_start':
      return `${style.label} evolution — attempt ${data.attemptNumber || 1}: ${data.strategySelected || 'unknown'}`;
    case 'evolution_result':
      return data.outcome === 'success'
        ? `✅ Evolution succeeded — ${data.capabilityCreated || 'capability built'}`
        : `❌ Attempt failed — ${data.failureReason || 'unknown reason'}`;
    case 'evolution_stuck_detected':
      return `🔵 Stuck detected — switching to: ${data.escapeStrategy || 'different approach'}`;
    case 'evolution_help_request':
      return `🆘 Help needed — ${data.taskSummary || 'evolution failed after 3 attempts'}`;
    default:
      return 'Evolution event';
  }
}

/**
 * Determine if an evolution_result event is a compact result that should
 * render with a tinted background and minimal collapsed view.
 */
function isCompactResult(eventType: EvolutionEventType) {
  return eventType === 'evolution_result';
}

/** Get tinted background color for result events based on outcome. */
function getResultTint(data: Record<string, unknown>): string {
  if (data.outcome === 'success') return 'rgba(34, 197, 94, 0.08)';
  if (data.outcome === 'failure') return 'rgba(239, 68, 68, 0.08)';
  return 'transparent';
}

/** Get border color override for result events. */
function getResultBorderColor(data: Record<string, unknown>): string | null {
  if (data.outcome === 'success') return '#22c55e';
  if (data.outcome === 'failure') return '#ef4444';
  return null;
}

export const EvolutionMessage: React.FC<EvolutionMessageProps> = ({ eventType, data }) => {
  const [expanded, setExpanded] = useState(false);
  const style = getTriggerStyle(eventType, data);
  const summary = getSummary(eventType, data, style);
  const compact = isCompactResult(eventType);
  const resultBorder = compact ? getResultBorderColor(data) : null;
  const resultTint = compact ? getResultTint(data) : 'transparent';

  // Compact collapsed view for result events: just icon + evolution ID
  if (compact && !expanded) {
    const outcomeIcon = data.outcome === 'success' ? '✅' : '❌';
    const evolutionId = (data.evolutionId as string) || '';
    return (
      <div
        style={{
          borderLeft: `3px solid ${resultBorder || style.color}`,
          padding: '6px 12px',
          margin: '4px 0',
          backgroundColor: resultTint,
          borderRadius: '4px',
          fontSize: '12px',
          cursor: 'pointer',
        }}
        onClick={() => setExpanded(true)}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => e.key === 'Enter' && setExpanded(true)}
        aria-expanded={false}
        aria-label={`Evolution result: ${data.outcome}${evolutionId ? ` (${evolutionId})` : ''}`}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          <span>{outcomeIcon}</span>
          <span style={{ color: 'var(--vscode-foreground, #ccc)', opacity: 0.85 }}>
            {evolutionId || (data.outcome === 'success' ? 'Evolved' : 'Failed')}
          </span>
          <span style={{ marginLeft: 'auto', opacity: 0.4, fontSize: '10px' }}>▶</span>
        </div>
      </div>
    );
  }

  return (
    <div
      style={{
        borderLeft: `3px solid ${resultBorder || style.color}`,
        padding: '8px 12px',
        margin: '4px 0',
        backgroundColor: compact ? resultTint : 'var(--vscode-editor-background, #1e1e1e)',
        borderRadius: '4px',
        fontSize: '13px',
        cursor: 'pointer',
      }}
      onClick={() => setExpanded(!expanded)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => e.key === 'Enter' && setExpanded(!expanded)}
      aria-expanded={expanded}
      aria-label={`Evolution event: ${summary}`}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
        <span>{style.icon}</span>
        <span style={{ color: 'var(--vscode-foreground, #ccc)' }}>{summary}</span>
        <span style={{ marginLeft: 'auto', opacity: 0.5, fontSize: '11px' }}>
          {expanded ? '▼' : '▶'}
        </span>
      </div>
      {expanded && (
        <pre
          style={{
            marginTop: '8px',
            padding: '8px',
            backgroundColor: 'var(--vscode-textBlockQuote-background, #252526)',
            borderRadius: '3px',
            fontSize: '12px',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            color: 'var(--vscode-foreground, #ccc)',
          }}
        >
          {JSON.stringify(data, null, 2)}
        </pre>
      )}
    </div>
  );
};
