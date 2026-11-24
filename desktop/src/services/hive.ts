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
  /** swarmai: the SwarmAI account (new Hives go here) · disconnected: the SwarmAI row while
   *  that account is not connected (nothing can be done with it) · other: another AWS
   *  account, its Hives can only be stopped or deleted · merged: its Hives moved to the
   *  SwarmAI account · own: no SwarmAI account connected, the account works as before */
  role: 'swarmai' | 'disconnected' | 'other' | 'merged' | 'own';
  hiveCount: number;
}

/** At most this many Hives (a guard against mis-operation; it counts machines). */
export const MAX_HIVES = 8;

/** Hives that count toward MAX_HIVES: every one whose EC2 instance may exist (a failed
 *  create without an instance does not). Same rule as the backend (hive/accounts.py). */
export function countedHives(instances: HiveInstance[]): number {
  return instances.filter((i) => i.status !== 'deleted' && !(i.status === 'error' && !i.ec2InstanceId)).length;
}

/** What the Hive surfaces show and allow, from the accounts list. With the SwarmAI
 *  account connected: new Hives only there, no adding accounts, and another account is
 *  listed only while it still holds Hives. Without it: everything as before. */
export function hiveAccountView(accounts: HiveAccount[]): {
  swarmai: HiveAccount | null; canAdd: boolean; deployable: HiveAccount[]; shown: HiveAccount[];
} {
  const swarmai = accounts.find((a) => a.role === 'swarmai') ?? null;
  if (!swarmai) {
    const own = accounts.filter((a) => a.role === 'own');
    const disconnected = accounts.filter((a) => a.role === 'disconnected' && a.hiveCount > 0);
    return { swarmai: null, canAdd: true, deployable: own, shown: [...disconnected, ...own] };
  }
  return {
    swarmai, canAdd: false, deployable: [swarmai],
    shown: accounts.filter((a) => a.role === 'swarmai' || (a.role === 'other' && a.hiveCount > 0)),
  };
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
  /** its own idle stop (backend core/hive_idle): null follows the default
   *  (hiveIdleStopMinutes), 0 is always on, N stops it after N idle minutes */
  idleStopMinutes?: number | null;
  /** the minutes after which the Host stops it now (its own, else the default); 0 = never */
  idleStopEffective?: number;
  /** whether it has an idle stop to set: only machines the Host stops when idle (EC2) */
  idleStopSettable?: boolean;
}

/** Transitional statuses that require polling */
export const TRANSITIONAL_STATUSES = ['pending', 'provisioning', 'installing', 'deleting', 'updating'];

/** True when release `latest` is newer than `current` by major.minor.patch. A suffix
 *  (a local build such as 2.3.0-hive1) is ignored, so the same core is never "newer";
 *  anything unparsable is not newer. */
export function isNewerVersion(latest: string | null | undefined, current: string | null | undefined): boolean {
  const core = (v: string | null | undefined) => {
    const m = /^v?(\d+)\.(\d+)\.(\d+)/.exec(v ?? '');
    return m ? [Number(m[1]), Number(m[2]), Number(m[3])] : null;
  };
  const a = core(latest);
  const b = core(current);
  if (!a || !b) return false;
  for (let i = 0; i < 3; i++) {
    if (a[i] !== b[i]) return a[i] > b[i];
  }
  return false;
}

/** Deploy progress steps derived from field presence */
export function getDeploySteps(inst: HiveInstance): { labelKey: string; done: boolean }[] {
  return [
    { labelKey: 'ui.hive.stepS3', done: !!inst.s3Bucket },
    { labelKey: 'ui.hive.stepIam', done: !!inst.iamRoleName },
    { labelKey: 'ui.hive.stepSecurityGroup', done: !!inst.securityGroupId },
    { labelKey: 'ui.hive.stepEc2', done: !!inst.ec2InstanceId },
    { labelKey: 'ui.hive.stepElasticIp', done: !!inst.ec2PublicIp },
    { labelKey: 'ui.hive.stepInstalling', done: inst.status === 'running' || !!inst.cloudfrontDomain },
    { labelKey: 'ui.hive.stepHealth', done: inst.status === 'running' || !!inst.cloudfrontDomain },
    { labelKey: 'ui.hive.stepCloudFront', done: !!inst.cloudfrontDomain },
  ];
}

export interface VerifyResult {
  success: boolean;
  accountId: string;
  checks: Record<string, { status: string; error?: string; [key: string]: unknown }>;
  error?: string;
}

/** One local Brain against one Hive (GET /hive/instances/{id}/brains). Only digests,
 *  times and counts come from the Hive; the reasons are the host's own text. */
export interface HiveBrainRow {
  brain: string;
  agent: 'ready' | 'invalid' | null;
  localDigest: string | null;
  localError: string | null;
  hiveDigest: string | null;
  installedAt: string | null;
  synced: boolean;
  hiveChangedFiles: number | null;
  running: number | null;
  dispatchable: boolean;
  reasons: string[];
  syncable: boolean;
  /** SwarmAI and General: synced to every Hive by default. */
  isDefault: boolean;
}

export interface HiveBrainStatus {
  hive: string;
  /** False when the Hive's version has no Brains endpoint (update it once). */
  supported: boolean;
  brains: HiveBrainRow[];
}

function brainRow(r: Record<string, unknown>): HiveBrainRow {
  const str = (v: unknown) => (typeof v === 'string' ? v : null);
  const num = (v: unknown) => (typeof v === 'number' ? v : null);
  return {
    brain: String(r.brain ?? ''),
    agent: (['ready', 'invalid'] as const).find((a) => a === r.agent) ?? null,
    localDigest: str(r.local_digest),
    localError: str(r.local_error),
    hiveDigest: str(r.hive_digest),
    installedAt: str(r.installed_at),
    synced: r.synced === true,
    hiveChangedFiles: num(r.hive_changed_files),
    running: num(r.running),
    dispatchable: r.dispatchable === true,
    reasons: Array.isArray(r.reasons) ? r.reasons.filter((x): x is string => typeof x === 'string') : [],
    syncable: r.syncable === true,
    isDefault: r.default === true,
  };
}

// ── snake_case → camelCase helper ──────────────────────────────────

function toCamel(obj: Record<string, unknown>): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(obj)) {
    const camelKey = k.replace(/_([a-z])/g, (_, c) => c.toUpperCase());
    result[camelKey] = v && typeof v === 'object' && !Array.isArray(v) ? toCamel(v as Record<string, unknown>) : v;
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

  /** Starts the update and returns; the Hive shows 'updating' until it ends 'running'
   *  (with the new version, or an errorMessage saying why not). */
  async updateInstance(id: string, version: string): Promise<void> {
    await api.post(`/hive/instances/${id}/update`, { version });
  },

  /** The latest published SwarmAI release (what a Hive can be updated to). */
  async latestVersion(): Promise<string> {
    const { data } = await api.get('/hive/latest-version');
    return (data as { version: string }).version;
  },

  // Brains on a Hive (design 2026-10-28-project-agent-flow §6)
  async brainStatus(id: string): Promise<HiveBrainStatus> {
    const { data } = await api.get(`/hive/instances/${id}/brains`);
    const d = data as { hive?: unknown; supported?: unknown; brains?: unknown };
    return {
      hive: String(d.hive ?? ''),
      supported: d.supported === true,
      brains: Array.isArray(d.brains) ? d.brains.map((r) => brainRow(r as Record<string, unknown>)) : [],
    };
  },

  async syncBrain(id: string, brain: string): Promise<void> {
    await api.post(`/hive/instances/${id}/brains/${encodeURIComponent(brain)}/sync`);
  },

  async syncDefaultBrains(id: string): Promise<void> {
    await api.post(`/hive/instances/${id}/brains/sync-defaults`);
  },

  /** Set one EC2 Hive's own idle stop: null = the default, 0 = always on, 30..480 minutes. */
  async setIdleStop(id: string, minutes: number | null): Promise<void> {
    await api.put(`/hive/instances/${id}/idle-stop`, { minutes });
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

  async getCredentials(id: string): Promise<{ authUser: string | null; authPassword: string | null }> {
    const { data } = await api.get(`/hive/instances/${id}/credentials`);
    return toCamel(data) as { authUser: string | null; authPassword: string | null };
  },

  /** Redeploys an errored Hive. When its instance still exists the backend refuses unless
   *  confirmDataLoss is set, and then saves every disk as a snapshot before deleting. */
  async retryInstance(id: string, confirmDataLoss = false): Promise<void> {
    await api.post(`/hive/instances/${id}/retry`, confirmDataLoss ? { confirm_data_loss: true } : undefined);
  },

  // ── SSH machines (backend routers/hive.py /ssh; design 2026-11-24-swarm-out-ssh-machine §8) ──
  async listSsh(): Promise<SshMachine[]> {
    const { data } = await api.get('/hive/ssh');
    return Array.isArray(data?.machines) ? data.machines : [];
  },
  async addSsh(body: SshMachineAdd): Promise<void> {
    await api.post('/hive/ssh', body);
  },
  async retrySsh(id: string): Promise<void> {
    await api.post(`/hive/ssh/${id}/retry`);
  },
  async setSshOptions(id: string, body: { general_tasks?: boolean; reaches_private?: boolean; cert_renew_hint?: string }): Promise<void> {
    await api.patch(`/hive/ssh/${id}`, body);
  },
  async removeSsh(id: string): Promise<{ left: string[]; data_kept: string }> {
    const { data } = await api.delete(`/hive/ssh/${id}`);
    return { left: Array.isArray(data?.left) ? data.left : [], data_kept: String(data?.data_kept ?? '') };
  },
};

/** An SSH machine as GET /hive/ssh shows it (never its Hive password). */
export interface SshMachine {
  id: string;
  name: string;
  status: string;
  version: string | null;
  /** the machine's description from its install ("x86_64 · 4 vCPU · 15 GB") */
  instance_type: string | null;
  error_message: string | null;
  config: {
    ssh_host: string | null;
    source_profile: string | null;
    reaches_private: boolean | null;
    general_tasks: boolean | null;
    mounts: string[] | null;
    cert_renew_hint: string | null;
  };
  /** null while it is not running; false: its tunnel or its Hive did not answer */
  reachable: boolean | null;
  /** its latest SSH certificate's end (Unix seconds), null when it has none */
  cert_valid_until: number | null;
  /** ssh's last line when its tunnel could not be built */
  tunnel_error: string;
}

export interface SshMachineAdd {
  ssh_host: string;
  source_profile: string;
  name?: string;
  mounts?: string[];
  cert_renew_hint?: string;
  reaches_private?: boolean;
  general_tasks?: boolean;
}
