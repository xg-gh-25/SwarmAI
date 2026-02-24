import api from './api';

export type BedrockAuthType = 'credentials' | 'bearer_token';

export interface APIConfigurationResponse {
  anthropic_api_key_set: boolean;
  anthropic_base_url: string | null;
  use_bedrock: boolean;
  bedrock_auth_type: BedrockAuthType;
  aws_access_key_id_set: boolean;
  aws_bearer_token_set: boolean;
  aws_region: string;
  available_models: string[];
  default_model: string;
}

export interface APIConfigurationRequest {
  anthropic_api_key?: string;
  anthropic_base_url?: string;
  use_bedrock?: boolean;
  bedrock_auth_type?: BedrockAuthType;
  aws_access_key_id?: string;
  aws_secret_access_key?: string;
  aws_session_token?: string;
  aws_bearer_token?: string;
  aws_region?: string;
  available_models?: string[];
  default_model?: string;
}

export const settingsService = {
  async getAPIConfiguration(): Promise<APIConfigurationResponse> {
    const response = await api.get<APIConfigurationResponse>('/settings');
    return response.data;
  },

  async updateAPIConfiguration(request: APIConfigurationRequest): Promise<APIConfigurationResponse> {
    const response = await api.put<APIConfigurationResponse>('/settings', request);
    return response.data;
  },
};
