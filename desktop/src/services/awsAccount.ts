/**
 * The SwarmAI AWS account (backend routers/aws_account.py; design
 * docs/design/2026-10-31-swarmai-aws-account.md). Step 1 is read-only: see the account,
 * re-verify it, run the capability checks.
 */
import api from './api';

const USER_ACTION = { headers: { 'X-Swarm-User-Action': 'aws-account' } };

export type AwsAccountState =
  | 'unconnected' | 'connected' | 'needs_login' | 'account_mismatch' | 'unverified';
export type CapabilityStatus = 'ok' | 'missing' | 'unknown' | 'not_applicable';

export interface Capability {
  status: CapabilityStatus;
  detail?: string | null;
  basis?: string | null;
}

export interface AwsAccountView {
  state: AwsAccountState;
  state_detail: string | null;
  account: {
    source: string | null;
    account_id: string | null;
    region: string | null;
    context: string | null;
    profile: string | null;
    caller_arn: string | null;
    verified_at: string | null;
    derived: boolean | null;
    ada: { role: string | null; provider: string | null } | null;
  } | null;
  chat_via: 'aws' | 'bedrock_key' | 'anthropic';
  capabilities: (Record<'bedrock' | 'hive' | 'storage' | 'voice', Capability> & { checked_at?: string }) | null;
  profile_file: string;
  supported: boolean;
  /** what the credential banner says when this account needs the user (else null) */
  remediation?: { message: string; fix_text: string; settings_tab: string } | null;
}

export const awsAccountService = {
  async get(): Promise<AwsAccountView> {
    return (await api.get<AwsAccountView>('/aws-account')).data;
  },
  // Work-doing POSTs carry X-Swarm-User-Action: a cross-site page cannot send it.
  async verify(): Promise<AwsAccountView> {
    return (await api.post<AwsAccountView>('/aws-account/verify', null, USER_ACTION)).data;
  },
  async check(): Promise<AwsAccountView> {
    return (await api.post<AwsAccountView>('/aws-account/check', null, USER_ACTION)).data;
  },
};
