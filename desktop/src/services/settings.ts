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
  anthropicBaseUrl: string | null;
  readonly awsCredentialsConfigured: boolean;
  readonly anthropicApiKeyConfigured: boolean;
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
};
