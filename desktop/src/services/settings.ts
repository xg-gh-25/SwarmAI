/**
 * Settings service for SwarmAI desktop app.
 *
 * Communicates with the backend Settings API (GET/PUT /api/settings) to
 * read and update application configuration stored in SwarmWS/config.json.
 *
 * Key exports:
 * - ``APIConfigurationResponse`` — Read-only config + credential status indicators
 * - ``APIConfigurationRequest``  — Partial-update request (no credential fields)
 * - ``settingsService``          — API methods for get/update configuration
 */
import api from './api';

// Transform settings response from snake_case (backend) to camelCase (frontend)
const toSettingsCamelCase = (data: Record<string, unknown>): APIConfigurationResponse => {
  return {
    useBedrock: data.use_bedrock as boolean,
    awsRegion: data.aws_region as string,
    anthropicBaseUrl: (data.anthropic_base_url as string | null) ?? null,
    availableModels: data.available_models as string[],
    defaultModel: data.default_model as string,
    claudeCodeDisableExperimentalBetas: data.claude_code_disable_experimental_betas as boolean,
    awsCredentialsConfigured: data.aws_credentials_configured as boolean,
    anthropicApiKeyConfigured: data.anthropic_api_key_configured as boolean,
  };
};

export interface APIConfigurationResponse {
  useBedrock: boolean;
  awsRegion: string;
  anthropicBaseUrl: string | null;
  availableModels: string[];
  defaultModel: string;
  claudeCodeDisableExperimentalBetas: boolean;
  /** Read-only: true if AWS credential chain resolves */
  awsCredentialsConfigured: boolean;
  /** Read-only: true if ANTHROPIC_API_KEY env var is set */
  anthropicApiKeyConfigured: boolean;
}

export interface APIConfigurationRequest {
  use_bedrock?: boolean;
  aws_region?: string;
  anthropic_base_url?: string;
  available_models?: string[];
  default_model?: string;
  claude_code_disable_experimental_betas?: boolean;
}

export const settingsService = {
  async getAPIConfiguration(): Promise<APIConfigurationResponse> {
    const response = await api.get<Record<string, unknown>>('/settings');
    return toSettingsCamelCase(response.data);
  },

  async updateAPIConfiguration(request: APIConfigurationRequest): Promise<APIConfigurationResponse> {
    const response = await api.put<Record<string, unknown>>('/settings', request);
    return toSettingsCamelCase(response.data);
  },
};
