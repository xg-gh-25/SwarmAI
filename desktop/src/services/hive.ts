/**
 * Hive cloud instance management service.
 *
 * CRUD for AWS accounts and Hive instances via /api/hive endpoints.
 */
import api from './api';

// ── Types ──────────────────────────────────────────────────────────

export interface HiveAccount {
  id: string;
  accountId: string;
  label: string;
  authMethod: string;
  defaultRegion: string;
  createdAt: string;
  verifiedAt: string | null;
}

export interface HiveInstance {
  id: string;
  name: string;
  accountRef: string;
  region: string;
  instanceType: string;
  ec2InstanceId: string | null;
  ec2PublicIp: string | null;
  cloudfrontDomain: string | null;
  status: string;
  version: string | null;
  errorMessage: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface VerifyResult {
  success: boolean;
  accountId: string;
  checks: Record<string, { status: string; error?: string; [key: string]: unknown }>;
  error?: string;
}

// ── snake_case → camelCase helper ──────────────────────────────────

function toCamel(obj: Record<string, unknown>): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(obj)) {
    const camelKey = k.replace(/_([a-z])/g, (_, c) => c.toUpperCase());
    result[camelKey] = v;
  }
  return result;
}

// ── Service ────────────────────────────────────────────────────────

export const hiveService = {
  // Accounts
  async listAccounts(): Promise<HiveAccount[]> {
    const { data } = await api.get('/hive/accounts');
    return (data as Record<string, unknown>[]).map(r => toCamel(r) as unknown as HiveAccount);
  },

  async createAccount(body: {
    accountId: string;
    label?: string;
    authMethod?: string;
    authConfig?: Record<string, string>;
    defaultRegion?: string;
  }): Promise<HiveAccount> {
    const { data } = await api.post('/hive/accounts', {
      account_id: body.accountId,
      label: body.label ?? '',
      auth_method: body.authMethod ?? 'access_keys',
      auth_config: body.authConfig ?? {},
      default_region: body.defaultRegion ?? 'us-east-1',
    });
    return toCamel(data) as unknown as HiveAccount;
  },

  async deleteAccount(id: string): Promise<void> {
    await api.delete(`/hive/accounts/${id}`);
  },

  async verifyAccount(id: string): Promise<VerifyResult> {
    const { data } = await api.post(`/hive/accounts/${id}/verify`);
    return toCamel(data) as unknown as VerifyResult;
  },

  // Instances
  async listInstances(): Promise<HiveInstance[]> {
    const { data } = await api.get('/hive/instances');
    return (data as Record<string, unknown>[]).map(r => toCamel(r) as unknown as HiveInstance);
  },

  async createInstance(body: {
    name: string;
    accountRef: string;
    region?: string;
    instanceType?: string;
  }): Promise<HiveInstance> {
    const { data } = await api.post('/hive/instances', {
      name: body.name,
      account_ref: body.accountRef,
      region: body.region ?? 'us-east-1',
      instance_type: body.instanceType ?? 'm7g.xlarge',
    });
    return toCamel(data) as unknown as HiveInstance;
  },

  async stopInstance(id: string): Promise<void> {
    await api.post(`/hive/instances/${id}/stop`);
  },

  async startInstance(id: string): Promise<void> {
    await api.post(`/hive/instances/${id}/start`);
  },

  async deleteInstance(id: string): Promise<void> {
    await api.delete(`/hive/instances/${id}`);
  },
};
