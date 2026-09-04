/**
 * Settings service — generic dict pass-through with snake↔camel transform.
 *
 * No per-field interfaces. The backend returns a plain dict from DEFAULT_CONFIG;
 * this service transforms keys generically. Only fields actively used by UI
 * components are typed in SettingsConfig — everything else passes through.
 *
 * Key exports:
 * - ``SettingsConfig``   — Typed wrapper for known fields + index signature
 * - ``settingsService``  — API methods for get/update configuration
 * - ``snakeToCamel``     — Generic key transform (exported for testing)
 * - ``camelToSnake``     — Generic key transform (exported for testing)
 */
import api from './api';

// ---------------------------------------------------------------------------
// Generic snake_case ↔ camelCase utilities
// ---------------------------------------------------------------------------

export function snakeToCamel(s: string): string {
  return s.replace(/_([a-z])/g, (_, c) => c.toUpperCase());
}

export function camelToSnake(s: string): string {
  return s.replace(/[A-Z]/g, (c) => `_${c.toLowerCase()}`);
}

function transformKeys<T>(
  obj: Record<string, unknown>,
  keyFn: (k: string) => string,
): T {
  // Shallow transform only — nested objects (bedrock_model_map, evolution)
  // keep their original key casing. This matches the backend contract where
  // nested dicts are opaque blobs, not individually-keyed config fields.
  const result: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(obj)) {
    result[keyFn(k)] = v;
  }
  return result as T;
}

// ---------------------------------------------------------------------------
// Typed wrapper — only fields actively used by SettingsPage
// ---------------------------------------------------------------------------

export interface SettingsConfig extends Record<string, unknown> {
  useBedrock: boolean;
  awsRegion: string;
  defaultModel: string;
  availableModels: string[];
  // Nested dict: short model name → Bedrock inference-profile id. The shallow
  // snake↔camel transform leaves nested keys untouched (they're model ids, not
  // config field names), so this round-trips intact.
  bedrockModelMap: Record<string, string>;
  thinkingMode: 'adaptive' | 'enabled' | 'disabled';
  thinkingEffort: 'low' | 'medium' | 'high' | 'xhigh' | 'max';
  anthropicBaseUrl: string | null;
  readonly awsCredentialsConfigured: boolean;
  readonly anthropicApiKeyConfigured: boolean;
}

/** One discovered Bedrock model (GET /settings/bedrock/models). */
export interface BedrockModel {
  shortName: string;
  bedrockId: string;
  isNew: boolean;
}

/** Fail-open response of the Bedrock discovery endpoint. */
export interface BedrockModelsResult {
  available: boolean;
  error: string | null;
  models: BedrockModel[];
}

// ---------------------------------------------------------------------------
// Service
// ---------------------------------------------------------------------------

export const settingsService = {
  async getAPIConfiguration(): Promise<SettingsConfig> {
    const response = await api.get<Record<string, unknown>>('/settings');
    return transformKeys<SettingsConfig>(response.data, snakeToCamel);
  },

  async updateAPIConfiguration(
    request: Record<string, unknown>,
  ): Promise<SettingsConfig> {
    const payload = transformKeys<Record<string, unknown>>(request, camelToSnake);
    const response = await api.put<Record<string, unknown>>('/settings', payload);
    return transformKeys<SettingsConfig>(response.data, snakeToCamel);
  },

  /**
   * Discover callable Claude models from Bedrock (auto-discovery).
   * Fail-open: on backend/AWS error returns { available:false, error, models:[] }
   * so the caller can keep the current model list instead of blanking it.
   */
  async getBedrockModels(): Promise<BedrockModelsResult> {
    const response = await api.get<{
      available: boolean;
      error: string | null;
      models: { short_name: string; bedrock_id: string; is_new: boolean }[];
    }>('/settings/bedrock/models');
    const d = response.data;
    return {
      available: !!d.available,
      error: d.error ?? null,
      models: (d.models ?? []).map((m) => ({
        shortName: m.short_name,
        bedrockId: m.bedrock_id,
        isNew: m.is_new,
      })),
    };
  },
};
