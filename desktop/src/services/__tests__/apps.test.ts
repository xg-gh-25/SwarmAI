/** Tests for the read-only Phase A Apps service contract. */
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { get } = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock('../api', () => ({ default: { get } }));

import { appsService } from '../apps';

beforeEach(() => get.mockReset());

describe('appsService', () => {
  it('normalizes the immutable registry response without inventing lifecycle controls', async () => {
    get.mockResolvedValue({ data: {
      schema_version: 1,
      host_contract_version: '1',
      current_profile: 'desktop-daemon',
      apps: [{
        app_id: 'pollinate', display_name: 'Pollinate', description: 'Media',
        version: '1.0.0', instance_mode: 'singleton', source_tier: 'built-in',
        evidence_state: 'catalog-only', lifecycle_controls: [],
        package_release_digest: 'a'.repeat(64), execution_release_digest: null,
        binding_map_digest: 'b'.repeat(64), proof_blockers: ['proof-not-run'],
        profile_accounting: [{ profile: 'desktop-daemon', contract_version: '1',
          capability_digest: 'c'.repeat(64), supported: true,
          blocked_resources: [], resources: [] }],
        resources: [], dependencies: [],
      }],
    }});
    const result = await appsService.list();
    expect(get).toHaveBeenCalledWith('/apps');
    expect(result.currentProfile).toBe('desktop-daemon');
    expect(result.apps[0].appId).toBe('pollinate');
    expect(result.apps[0].evidenceState).toBe('catalog-only');
    expect(result.apps[0].lifecycleControls).toEqual([]);
  });

  it('fails closed when the API reports an unknown runtime profile', async () => {
    get.mockResolvedValue({ data: {
      schema_version: 1,
      host_contract_version: '1',
      current_profile: 'invented',
      apps: [],
    }});
    const result = await appsService.list();
    expect(result.currentProfile).toBeNull();
  });

  it('maps verified upstream source metadata for external Apps', async () => {
    get.mockResolvedValue({ data: {
      schema_version: 1,
      host_contract_version: '1',
      current_profile: 'desktop-daemon',
      apps: [{
        app_id: 'research', display_name: 'Research', description: 'Research',
        version: '1.0.0', instance_mode: 'singleton', source_tier: 'external',
        evidence_state: 'catalog-only', lifecycle_controls: [],
        package_release_digest: 'a'.repeat(64), execution_release_digest: null,
        binding_map_digest: 'b'.repeat(64), proof_blockers: ['proof-not-run'],
        profile_accounting: [], resources: [], dependencies: [],
        upstream_source: { sources: [{
          id: 'mattpocock-skills', repository: 'https://github.com/mattpocock/skills',
          commit: 'c'.repeat(40), license: 'MIT',
        }] },
      }],
    }});
    const result = await appsService.list();
    expect(result.apps[0].sourceTier).toBe('external');
    expect(result.apps[0].upstreamSource?.sources[0].license).toBe('MIT');
  });
});
