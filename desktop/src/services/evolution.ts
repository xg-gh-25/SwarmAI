/**
 * Evolution service for SwarmAI desktop app.
 *
 * TypeScript interfaces for evolution SSE event payloads and
 * toCamelCase conversion for snake_case → camelCase transformation.
 * Follows the pattern established in ``settings.ts``.
 *
 * Key exports:
 * - ``EvolutionStartEvent``       — Payload for evolution_start SSE events
 * - ``EvolutionResultEvent``      — Payload for evolution_result SSE events
 * - ``EvolutionStuckEvent``       — Payload for evolution_stuck_detected SSE events
 * - ``EvolutionHelpRequestEvent`` — Payload for evolution_help_request SSE events
 * - ``EvolutionEventType``        — Union type of all evolution event types
 * - ``EvolutionConfig``           — Config shape for evolution settings
 * - ``toEvolutionConfigCamelCase``— snake_case → camelCase converter for config
 * - ``evolutionService``          — API methods for evolution config
 */
import api from './api';

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

export interface EvolutionConfig {
  enabled: boolean;
  maxRetries: number;
  verificationTimeoutSeconds: number;
  autoApproveSkills: boolean;
  autoApproveScripts: boolean;
  autoApproveInstalls: boolean;
  proactiveEnabled: boolean;
  stuckDetectionEnabled: boolean;
}

export const toEvolutionConfigCamelCase = (data: Record<string, unknown>): EvolutionConfig => ({
  enabled: (data.enabled as boolean) ?? true,
  maxRetries: (data.max_retries as number) ?? 3,
  verificationTimeoutSeconds: (data.verification_timeout_seconds as number) ?? 120,
  autoApproveSkills: (data.auto_approve_skills as boolean) ?? false,
  autoApproveScripts: (data.auto_approve_scripts as boolean) ?? false,
  autoApproveInstalls: (data.auto_approve_installs as boolean) ?? false,
  proactiveEnabled: (data.proactive_enabled as boolean) ?? true,
  stuckDetectionEnabled: (data.stuck_detection_enabled as boolean) ?? true,
});

export const evolutionService = {
  async getConfig(): Promise<EvolutionConfig> {
    const response = await api.get<Record<string, unknown>>('/settings');
    const evolution = (response.data as Record<string, unknown>).evolution as Record<string, unknown>;
    return toEvolutionConfigCamelCase(evolution || {});
  },

  async updateConfig(config: Partial<EvolutionConfig>): Promise<EvolutionConfig> {
    // Convert camelCase fields back to snake_case for the backend
    const payload: Record<string, unknown> = {};
    if (config.enabled !== undefined) payload.enabled = config.enabled;
    if (config.maxRetries !== undefined) payload.max_retries = config.maxRetries;
    if (config.verificationTimeoutSeconds !== undefined) payload.verification_timeout_seconds = config.verificationTimeoutSeconds;
    if (config.autoApproveSkills !== undefined) payload.auto_approve_skills = config.autoApproveSkills;
    if (config.autoApproveScripts !== undefined) payload.auto_approve_scripts = config.autoApproveScripts;
    if (config.autoApproveInstalls !== undefined) payload.auto_approve_installs = config.autoApproveInstalls;
    if (config.proactiveEnabled !== undefined) payload.proactive_enabled = config.proactiveEnabled;
    if (config.stuckDetectionEnabled !== undefined) payload.stuck_detection_enabled = config.stuckDetectionEnabled;

    const response = await api.put<Record<string, unknown>>('/settings', { evolution: payload });
    const evolution = (response.data as Record<string, unknown>).evolution as Record<string, unknown>;
    return toEvolutionConfigCamelCase(evolution || {});
  },
};
