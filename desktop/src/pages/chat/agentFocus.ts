/** Cross-turn focus request for an Agent tool call rendered in an earlier message. */
export const FOCUS_AGENT_EVENT = 'swarm:focus-agent';

export interface FocusAgentDetail {
  toolUseId: string;
  sessionId?: string;
}

/** Ask the message that owns this Agent call to reveal its folded ancestors and focus it. */
export function focusAgent(toolUseId: string, sessionId?: string): void {
  window.dispatchEvent(new CustomEvent<FocusAgentDetail>(FOCUS_AGENT_EVENT, {
    detail: { toolUseId, sessionId },
  }));
}
