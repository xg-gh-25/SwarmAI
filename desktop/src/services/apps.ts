/**
 * Read-only client for the App Platform Phase A catalog.
 *
 * Exports typed catalog/profile evidence only. No lifecycle mutation exists.
 */
import api from './api';

export type AppEvidenceState = 'catalog-only' | 'development' | 'product-proven';
export type AppHostProfile = 'desktop-daemon' | 'desktop-subprocess' | 'hive';

export interface AppProfileAccounting {
  profile: AppHostProfile;
  contractVersion: string;
  capabilityDigest: string;
  supported: boolean;
  blockedResources: string[];
}

export interface AppCatalogEntry {
  appId: string;
  displayName: string;
  description: string;
  version: string;
  instanceMode: string;
  sourceTier: 'built-in' | 'external';
  evidenceState: AppEvidenceState;
  lifecycleControls: string[];
  packageReleaseDigest: string;
  executionReleaseDigest: string | null;
  bindingMapDigest: string;
  proofBlockers: string[];
  profileAccounting: AppProfileAccounting[];
  resourceCount: number;
}

export interface AppsCatalog {
  schemaVersion: number;
  hostContractVersion: string;
  currentProfile: AppHostProfile | null;
  apps: AppCatalogEntry[];
}


interface RawProfile {
  profile: AppHostProfile;
  contract_version: string;
  capability_digest: string;
  supported: boolean;
  blocked_resources?: string[];
}

const APP_HOST_PROFILES = new Set<AppHostProfile>([
  'desktop-daemon',
  'desktop-subprocess',
  'hive',
]);

function mapCurrentProfile(value: unknown): AppHostProfile | null {
  return typeof value === 'string' && APP_HOST_PROFILES.has(value as AppHostProfile)
    ? value as AppHostProfile
    : null;
}

interface RawApp extends Record<string, unknown> {
  profile_accounting?: RawProfile[];
  resources?: unknown[];
}

function mapApp(raw: RawApp): AppCatalogEntry {
  return {
    appId: raw.app_id as string,
    displayName: raw.display_name as string,
    description: (raw.description as string) ?? '',
    version: raw.version as string,
    instanceMode: raw.instance_mode as string,
    sourceTier: raw.source_tier as AppCatalogEntry['sourceTier'],
    evidenceState: raw.evidence_state as AppEvidenceState,
    lifecycleControls: (raw.lifecycle_controls as string[]) ?? [],
    packageReleaseDigest: raw.package_release_digest as string,
    executionReleaseDigest: (raw.execution_release_digest as string | null) ?? null,
    bindingMapDigest: raw.binding_map_digest as string,
    proofBlockers: (raw.proof_blockers as string[]) ?? [],
    profileAccounting: (raw.profile_accounting ?? []).map((profile) => ({
      profile: profile.profile,
      contractVersion: profile.contract_version,
      capabilityDigest: profile.capability_digest,
      supported: profile.supported,
      blockedResources: profile.blocked_resources ?? [],
    })),
    resourceCount: raw.resources?.length ?? 0,
  };
}

export const appsService = {
  async list(): Promise<AppsCatalog> {
    const { data } = await api.get('/apps');
    return {
      schemaVersion: data.schema_version ?? 1,
      hostContractVersion: data.host_contract_version ?? '',
      currentProfile: mapCurrentProfile(data.current_profile),
      apps: ((data.apps ?? []) as RawApp[]).map(mapApp),
    };
  },
};
