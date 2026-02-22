import api from './api';
import type {
  Marketplace,
  MarketplaceCreateRequest,
  MarketplaceSyncResponse,
  Plugin,
  PluginInstallRequest,
  PluginUninstallResponse,
  AvailablePlugin,
} from '../types';

// Convert snake_case to camelCase for Marketplace
const toMarketplaceCamelCase = (data: Record<string, unknown>): Marketplace => {
  // Parse cached_plugins if it's a string
  let cachedPlugins = data.cached_plugins as AvailablePlugin[] | string;
  if (typeof cachedPlugins === 'string') {
    try {
      cachedPlugins = JSON.parse(cachedPlugins) as AvailablePlugin[];
    } catch {
      cachedPlugins = [];
    }
  }

  return {
    id: data.id as string,
    name: data.name as string,
    description: data.description as string | undefined,
    type: data.type as 'git' | 'http' | 'local',
    url: data.url as string,
    branch: (data.branch as string) || 'main',
    isActive: Boolean(data.is_active),
    lastSyncedAt: data.last_synced_at as string | undefined,
    cachedPlugins: (cachedPlugins as AvailablePlugin[]) || [],
    createdAt: data.created_at as string,
    updatedAt: data.updated_at as string,
  };
};

// Convert snake_case to camelCase for Plugin
const toPluginCamelCase = (data: Record<string, unknown>): Plugin => {
  // Helper to parse JSON strings
  const parseList = (val: unknown): string[] => {
    if (typeof val === 'string') {
      try {
        return JSON.parse(val) as string[];
      } catch {
        return [];
      }
    }
    return (val as string[]) || [];
  };

  return {
    id: data.id as string,
    name: data.name as string,
    description: data.description as string | undefined,
    version: data.version as string,
    marketplaceId: data.marketplace_id as string,
    marketplaceName: data.marketplace_name as string | undefined,
    author: data.author as string | undefined,
    license: data.license as string | undefined,
    installedSkills: parseList(data.installed_skills),
    installedCommands: parseList(data.installed_commands),
    installedAgents: parseList(data.installed_agents),
    installedHooks: parseList(data.installed_hooks),
    installedMcpServers: parseList(data.installed_mcp_servers),
    status: (data.status as 'installed' | 'disabled' | 'error') || 'installed',
    installPath: data.install_path as string | undefined,
    installedAt: data.installed_at as string,
    updatedAt: data.updated_at as string,
  };
};

export const pluginsService = {
  // ============== Marketplace Methods ==============

  async listMarketplaces(): Promise<Marketplace[]> {
    const response = await api.get<Record<string, unknown>[]>('/plugins/marketplaces');
    return response.data.map(toMarketplaceCamelCase);
  },

  async getMarketplace(marketplaceId: string): Promise<Marketplace> {
    const response = await api.get<Record<string, unknown>>(`/plugins/marketplaces/${marketplaceId}`);
    return toMarketplaceCamelCase(response.data);
  },

  async createMarketplace(data: MarketplaceCreateRequest): Promise<Marketplace> {
    const response = await api.post<Record<string, unknown>>('/plugins/marketplaces', {
      name: data.name,
      description: data.description,
      type: data.type,
      url: data.url,
      branch: data.branch || 'main',
    });
    return toMarketplaceCamelCase(response.data);
  },

  async syncMarketplace(marketplaceId: string): Promise<MarketplaceSyncResponse> {
    const response = await api.post<Record<string, unknown>>(
      `/plugins/marketplaces/${marketplaceId}/sync`
    );
    return {
      marketplaceId: response.data.marketplace_id as string,
      marketplaceName: response.data.marketplace_name as string,
      isMarketplace: response.data.is_marketplace as boolean,
      pluginsFound: response.data.plugins_found as number,
      plugins: response.data.plugins as AvailablePlugin[],
      syncedAt: response.data.synced_at as string,
    };
  },

  async deleteMarketplace(marketplaceId: string): Promise<void> {
    await api.delete(`/plugins/marketplaces/${marketplaceId}`);
  },

  // ============== Plugin Methods ==============

  async listPlugins(): Promise<Plugin[]> {
    const response = await api.get<Record<string, unknown>[]>('/plugins/plugins');
    return response.data.map(toPluginCamelCase);
  },

  async getPlugin(pluginId: string): Promise<Plugin> {
    const response = await api.get<Record<string, unknown>>(`/plugins/plugins/${pluginId}`);
    return toPluginCamelCase(response.data);
  },

  async installPlugin(data: PluginInstallRequest): Promise<Plugin> {
    const response = await api.post<Record<string, unknown>>('/plugins/plugins/install', {
      plugin_name: data.pluginName,
      marketplace_id: data.marketplaceId,
      version: data.version,
    });
    return toPluginCamelCase(response.data);
  },

  async uninstallPlugin(pluginId: string): Promise<PluginUninstallResponse> {
    const response = await api.delete<Record<string, unknown>>(`/plugins/plugins/${pluginId}`);
    return {
      pluginId: response.data.plugin_id as string,
      removedSkills: (response.data.removed_skills as string[]) || [],
      removedCommands: (response.data.removed_commands as string[]) || [],
      removedAgents: (response.data.removed_agents as string[]) || [],
      removedHooks: (response.data.removed_hooks as string[]) || [],
    };
  },

  async disablePlugin(pluginId: string): Promise<{ status: string; pluginId: string }> {
    const response = await api.post<Record<string, unknown>>(`/plugins/plugins/${pluginId}/disable`);
    return {
      status: response.data.status as string,
      pluginId: response.data.plugin_id as string,
    };
  },

  async enablePlugin(pluginId: string): Promise<{ status: string; pluginId: string }> {
    const response = await api.post<Record<string, unknown>>(`/plugins/plugins/${pluginId}/enable`);
    return {
      status: response.data.status as string,
      pluginId: response.data.plugin_id as string,
    };
  },
};
