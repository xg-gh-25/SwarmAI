/**
 * Expert Library service for the SwarmAI desktop app (R2 Experts visualization).
 *
 * Communicates with the backend Experts API (GET /api/experts), a read-only
 * projection of ExpertManager.scan_experts() grouped by domain. Mirrors
 * skills.ts: a snake→camel field mapper + an axios-based fetch. No mutation —
 * the expert tree is a static SSOT (backend/experts/).
 *
 * Key exports:
 * - ``expertsService``   — API method for the grouped expert registry
 * - ``toCamelExpert``    — Snake-to-camel field mapper (exported for testing)
 */
import api from './api';
import type { Expert, ExpertsByDomain } from '../types';

// Convert a snake_case backend expert into the camelCase frontend Expert.
export const toCamelExpert = (data: Record<string, unknown>): Expert => {
  return {
    // All string fields use ?? (nullish) consistently — the backend ExpertResponse
    // declares them non-optional (pydantic enforces presence), so we trust the SSOT
    // and only default a genuinely absent (undefined) key, never an intentional
    // empty string. visibility keeps a semantic 'public' fallback for an empty value.
    expertId: (data.expert_id as string) ?? '',
    displayNameZh: (data.display_name_zh as string) ?? '',
    displayNameEn: (data.display_name_en as string) ?? '',
    domain: (data.domain as string) ?? '',
    scope: (data.scope as string) ?? '',
    visibility: (data.visibility as string) || 'public',
    isValid: (data.is_valid as boolean) ?? false,
  };
};

export const expertsService = {
  // List all experts grouped by domain. The backend returns
  // {domain: [expert]}; we map each expert to camelCase, preserving the grouping.
  async list(): Promise<ExpertsByDomain> {
    const response = await api.get<Record<string, Record<string, unknown>[]>>('/experts');
    const grouped: ExpertsByDomain = {};
    for (const [domain, experts] of Object.entries(response.data ?? {})) {
      grouped[domain] = experts.map(toCamelExpert);
    }
    return grouped;
  },
};
