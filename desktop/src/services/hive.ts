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
  ownerName: string | null;
  hiveType: string;
  accountRef: string;
  region: string;
  instanceType: string;
  ec2InstanceId: string | null;
  ec2PublicIp: string | null;
  elasticIpAllocId: string | null;
  securityGroupId: string | null;
  iamRoleName: string | null;
  cloudfrontDistId: string | null;
  cloudfrontDomain: string | null;
  s3Bucket: string | null;
  authUser: string | null;
  authPassword: string | null;
  status: string;
  version: string | null;
  errorMessage: string | null;
  createdAt: string;
  updatedAt: string;
}

/** Transitional statuses that require polling */
export const TRANSITIONAL_STATUSES = ['pending', 'provisioning', 'installing', 'deleting'];

/** Deploy progress steps derived from field presence */
export function getDeploySteps(inst: HiveInstance): { label: string; done: boolean }[] {
  return [
    { label: 'S3 bucket ready', done: !!inst.s3Bucket },
    { label: 'IAM Role created', done: !!inst.iamRoleName },
    { label: 'Security Group configured', done: !!inst.securityGroupId },
    { label: 'EC2 launched', done: !!inst.ec2InstanceId },
    { label: 'Elastic IP assigned', done: !!inst.ec2PublicIp },
    { label: 'Installing SwarmAI...', done: inst.status === 'running' || !!inst.cloudfrontDomain },
    { label: 'Backend health check', done: inst.status === 'running' || !!inst.cloudfrontDomain },
    { label: 'CloudFront HTTPS', done: !!inst.cloudfrontDomain },
  ];
}

export interface VerifyResult {
  success: boolean;
  accountId: string;
  checks: Record<string, { status: string; error?: string; [key: string]: unknown }>;
  error?: string;
}

// ── snake_case → camelCase helper ──────────────────────────────────

function toCamel(obj: Record<string, any>): any {
  const result: Record<string, any> = {};
  for (const [k, v] of Object.entries(obj)) {
    const camelKey = k.replace(/_([a-z])/g, (_, c) => c.toUpperCase());
    result[camelKey] = v && typeof v === 'object' && !Array.isArray(v) ? toCamel(v) : v;
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
    ownerName?: string;
    hiveType?: string;
  }): Promise<HiveInstance> {
    const { data } = await api.post('/hive/instances', {
      name: body.name,
      account_ref: body.accountRef,
      region: body.region ?? 'us-east-1',
      instance_type: body.instanceType ?? 'm7g.xlarge',
      owner_name: body.ownerName ?? null,
      hive_type: body.hiveType ?? 'shared',
    });
    return toCamel(data) as unknown as HiveInstance;
  },

  async updateInstance(id: string, version: string): Promise<void> {
    await api.post(`/hive/instances/${id}/update`, { version });
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
