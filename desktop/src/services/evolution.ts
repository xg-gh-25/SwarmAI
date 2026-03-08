/**
 * Evolution SSE event types for SwarmAI desktop app.
 *
 * TypeScript interfaces for evolution SSE event payloads consumed by
 * the ``EvolutionMessage`` chat component. Evolution is always enabled
 * with no user-configurable settings — this module only defines the
 * event type contracts.
 *
 * Key exports:
 * - ``EvolutionStartEvent``       — Payload for evolution_start SSE events
 * - ``EvolutionResultEvent``      — Payload for evolution_result SSE events
 * - ``EvolutionStuckEvent``       — Payload for evolution_stuck_detected SSE events
 * - ``EvolutionHelpRequestEvent`` — Payload for evolution_help_request SSE events
 * - ``EvolutionEventType``        — Union type of all evolution event types
 */

export interface EvolutionStartEvent {
  triggerType: 'reactive' | 'proactive' | 'stuck';
  description: string;
  strategySelected: string;
  attemptNumber: number;
  principleApplied: string | null;
}

export interface EvolutionResultEvent {
  outcome: 'success' | 'failure';
  durationMs: number;
  capabilityCreated: string | null;
  evolutionId: string | null;
  failureReason: string | null;
}

export interface EvolutionStuckEvent {
  detectedSignals: string[];
  triedSummary: string;
  escapeStrategy: string;
}

export interface EvolutionHelpRequestEvent {
  taskSummary: string;
  triggerType: string;
  attempts: Array<{ strategy: string; failureReason: string }>;
  suggestedNextStep: string;
}

export type EvolutionEventType =
  | 'evolution_start'
  | 'evolution_result'
  | 'evolution_stuck_detected'
  | 'evolution_help_request';
