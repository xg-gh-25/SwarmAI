/**
 * The SwarmAI AWS account (backend routers/aws_account.py; design
 * docs/design/2026-10-31-swarmai-aws-account.md): see it, detect the accounts this
 * machine can reach, sign in (ADA, AWS SSO in the app, AWS login, keys, a profile),
 * connect or change it, check its capabilities, and set how many Hives it runs.
 */
import api from './api';
import { settingsService } from './settings';
import { systemService } from './system';

const USER_ACTION = { headers: { 'X-Swarm-User-Action': 'aws-account' } };

export type AwsAccountState =
  | 'unconnected' | 'connected' | 'needs_login' | 'account_mismatch' | 'unverified';
export type CapabilityStatus = 'ok' | 'missing' | 'unknown' | 'not_applicable';
export type AwsSource = 'ada' | 'sso' | 'login' | 'profile' | 'keys' | 'instance_role';

export interface Capability {
  status: CapabilityStatus;
  detail?: string | null;
  basis?: string | null;
  missing?: string[];
  no_default_vpc?: boolean;
  region?: string;
}

export interface FleetStatus {
  state: 'ready' | 'short' | 'over' | 'cannot_build' | 'unavailable';
  desired: number | null;
  counted: number;
  counted_all?: number;
  max?: number;
  short?: number;
  over?: number;
  instance_type?: string;
  region?: string;
  monthly_usd?: number;
  dismissed?: boolean;
  capability?: Capability;
  /** monthly estimate per instance type (shown, never a cap) */
  prices?: Record<string, number>;
  /** the Hives that count, oldest first */
  hives?: { id: string; name: string; instance_type: string; region: string; status: string; created_at: string }[];
}

export interface AwsAccountView {
  state: AwsAccountState;
  state_detail: string | null;
  account: {
    source: AwsSource | null;
    account_id: string | null;
    region: string | null;
    context: string | null;
    profile: string | null;
    caller_arn: string | null;
    verified_at: string | null;
    derived: boolean | null;
    ada: { role: string | null; provider: string | null } | null;
    sso?: { start_url: string | null; sso_region: string | null; role: string | null } | null;
  } | null;
  chat_via: 'aws' | 'bedrock_key' | 'anthropic';
  /** the main region: Bedrock, cloud storage, voice, the default for new Hives */
  region?: string;
  capabilities: (Record<'bedrock' | 'hive' | 'storage' | 'voice', Capability> & { checked_at?: string }) | null;
  profile_file: string;
  supported: boolean;
  /** what the credential banner says if the credentials stop working: for the account
   *  when it needs the user (else null); without an account in use (no record, a Hive),
   *  always the fix for the legacy sign-in method — the banner shows only on "expired" */
  remediation?: { message: string; fix_text: string; settings_tab: string; action?: RemediationAction } | null;
  /** the Hive count and status (null on a Hive, or when it could not be read) */
  hive?: FleetStatus | null;
}

/** The one-click fix for a sign-in problem (backend core/auth_remediation `action`; design
 *  2026-11-01-settings-account-merge §4.2). A terminal command is built by the backend only. */
export type RemediationAction =
  | { kind: 'terminal'; command: string; title: string }
  | { kind: 'sign_in' }
  | { kind: 'settings'; tab: string };

/** One profile found on this machine (stage 1: what its configuration says). */
export interface DetectRow {
  profile: string;            // "" = ADA's own profile
  kind: 'ada' | 'ada_default' | 'process' | 'sso' | 'login' | 'assume_role' | 'keys';
  verify: 'auto' | 'check' | 'login';
  account_id: string | null;
  role: string | null;
  detail: string | null;
  sso: { start_url: string | null; sso_region: string | null } | null;
  ada: { role: string; provider: string } | null;
}

export interface DetectResult {
  rows: DetectRow[];
  context: 'internal' | 'external';
  login: { available: boolean; reason: 'no_cli' | 'old_cli' | null; version: string | null };
}

export interface VerifyRowResult {
  status: 'verified' | 'invalid' | 'needs_login' | 'failed' | 'timeout' | 'check';
  account_id?: string;
  arn?: string;
  detail?: string;
}

export interface Candidate {
  source: AwsSource;
  account_id?: string;
  region: string;
  context?: 'internal' | 'external';
  ada?: { role: string; provider: string };
  profile?: string;
  sso?: { start_url: string; sso_region: string; role: string };
  /** the key ID ``storeKeys`` answered (keys are kept per ID, never replaced) */
  keys_id?: string;
  /** the SwarmAI login profile the AWS login signed in to */
  login?: { profile: string };
}

export interface SsoStarted { flow_id: string; user_code: string; verification_url: string; interval: number; expires_in: number }
export interface SsoAccount { account_id: string; name: string | null; roles: string[] }
export type SsoPoll = { status: 'pending' } | { status: 'done'; accounts: SsoAccount[] };

/** The error detail the account routes answer with (409 / 422 / 401). */
export interface AccountProblem {
  kind: string;
  message: string;
  expected?: string;
  actual?: string;
  account_id?: string;
  hives?: { id: string; name: string; status: string }[];
  can_keep?: boolean;
}

export function problemOf(e: unknown): AccountProblem {
  const detail = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (detail && typeof detail === 'object' && 'kind' in (detail as object)) return detail as AccountProblem;
  if (typeof detail === 'string') return { kind: 'error', message: detail };
  return { kind: 'error', message: e instanceof Error ? e.message : String(e) };
}

export const awsAccountService = {
  async get(): Promise<AwsAccountView> {
    return (await api.get<AwsAccountView>('/aws-account')).data;
  },
  // Work-doing POSTs carry X-Swarm-User-Action: a cross-site page cannot send it.
  async verify(): Promise<AwsAccountView> {
    return (await api.post<AwsAccountView>('/aws-account/verify', null, USER_ACTION)).data;
  },
  async check(region?: string): Promise<AwsAccountView> {
    return (await api.post<AwsAccountView>('/aws-account/check', region ? { region } : null, USER_ACTION)).data;
  },
  async detect(): Promise<DetectResult> {
    return (await api.get<DetectResult>('/aws-account/detect')).data;
  },
  /** automatic checks only: the backend refuses to run a row that could prompt */
  async verifyRow(profile: string): Promise<VerifyRowResult> {
    return (await api.post<VerifyRowResult>('/aws-account/detect/verify', { profile }, USER_ACTION)).data;
  },
  /** the person clicked "Check": may run a program that prompts (Touch ID, MFA) */
  async checkRow(profile: string): Promise<VerifyRowResult> {
    return (await api.post<VerifyRowResult>('/aws-account/detect/check', { profile }, USER_ACTION)).data;
  },
  async connect(candidate: Candidate, oldHives?: 'keep'): Promise<AwsAccountView> {
    return (await api.post<AwsAccountView>('/aws-account/connect',
      { candidate, old_hives: oldHives ?? null }, USER_ACTION)).data;
  },
  async ssoStart(startUrl: string, ssoRegion: string): Promise<SsoStarted> {
    return (await api.post<SsoStarted>('/aws-account/sso/start',
      { start_url: startUrl, sso_region: ssoRegion }, USER_ACTION)).data;
  },
  async ssoPoll(flowId: string): Promise<SsoPoll> {
    return (await api.post<SsoPoll>('/aws-account/sso/poll', { flow_id: flowId }, USER_ACTION)).data;
  },
  async loginStart(region: string): Promise<{ status: string; profile?: string }> {
    return (await api.post<{ status: string; profile?: string }>('/aws-account/login/start', { region }, USER_ACTION)).data;
  },
  async loginStatus(): Promise<{ status: 'idle' | 'running' | 'done' | 'failed'; detail?: string; profile?: string }> {
    return (await api.get('/aws-account/login/status')).data;
  },
  async storeKeys(accessKeyId: string, secretAccessKey: string): Promise<{ stored: boolean; keys_id: string }> {
    return (await api.post<{ stored: boolean; keys_id: string }>('/aws-account/keys',
      { access_key_id: accessKeyId, secret_access_key: secretAccessKey }, USER_ACTION)).data;
  },
  async permissionTemplate(): Promise<Record<string, unknown>> {
    return (await api.get('/aws-account/permission-template')).data;
  },
  /** create the region's default VPC (the Hive check found none), then check it again */
  async createDefaultVpc(region: string): Promise<AwsAccountView> {
    return (await api.post<AwsAccountView>('/aws-account/default-vpc', { region }, USER_ACTION)).data;
  },
  /** Chat goes through this account's Bedrock again (from a Bedrock API key). */
  async useForChat(account: NonNullable<AwsAccountView['account']>): Promise<AwsAccountView> {
    const method = account.source === 'ada' ? 'ada' : 'sso';
    await settingsService.updateAPIConfiguration({ use_bedrock: true, auth_method: method });
    await systemService.setAuthMethod(method, account.context ?? undefined);
    return this.get();
  },
  async setFleet(body: { desired_count?: number; instance_type?: string; region?: string; dismissed?: boolean }): Promise<AwsAccountView> {
    return (await api.put<AwsAccountView>('/aws-account/hive-fleet', body, USER_ACTION)).data;
  },
};
