/**
 * Tests for the Hive nav-card wiring + status dot (run_b450108e).
 *
 * Covers the SSOT + security invariants and the calm-dot decision:
 *  • hive IS a registered fullscreen overlay id (∈ OVERLAY_IDS)
 *  • hive is NOT agent-openable (∉ ALL_SHOW_EVENTS, ∉ ui_action allowlist) — it controls
 *    AWS credentials + live cloud infra, same security boundary as library/settings/eval
 *  • deriveHiveStatusDot: error > provisioning > running priority; silent at 0 / all-stopped
 *
 * These are pure/list assertions — no full LeftSidebar render (which needs the whole
 * provider stack); the dot decision is extracted to a pure exported helper for this.
 */
import { describe, it, expect } from 'vitest';
import { OVERLAY_IDS } from './overlayIds';
import { ALL_SHOW_EVENTS } from './useExclusiveOverlay';
import { UI_COMMAND_TABLE } from '../../utils/uiCommands';
import { deriveHiveStatusDot } from './ThreeColumnLayout';

const inst = (status: string, hiveType = 'my') => ({ status, hiveType });

describe('Hive nav card — SSOT + security boundary', () => {
  it('hive is a registered fullscreen overlay id', () => {
    expect((OVERLAY_IDS as readonly string[]).includes('hive')).toBe(true);
  });

  it('hive is NOT agent-openable — absent from ALL_SHOW_EVENTS (AWS-cred security boundary)', () => {
    expect((ALL_SHOW_EVENTS as readonly string[]).includes('swarm:show-hive')).toBe(false);
    // same posture as the other nav-card-only surfaces
    expect((ALL_SHOW_EVENTS as readonly string[]).includes('swarm:show-settings')).toBe(false);
    expect((ALL_SHOW_EVENTS as readonly string[]).includes('swarm:show-eval')).toBe(false);
  });

  it('hive is NOT in the ui_action allowlist (the agent cannot dispatch it)', () => {
    expect(Object.keys(UI_COMMAND_TABLE).includes('show-hive')).toBe(false);
  });
});

describe('deriveHiveStatusDot — calm, signal-driven', () => {
  it('returns undefined for 0 instances (silent — no badge)', () => {
    expect(deriveHiveStatusDot([])).toBeUndefined();
    expect(deriveHiveStatusDot(undefined)).toBeUndefined();
  });

  it('returns undefined when every instance is stopped (silent)', () => {
    expect(deriveHiveStatusDot([inst('stopped'), inst('stopped')])).toBeUndefined();
  });

  it('green when any instance is running', () => {
    const dot = deriveHiveStatusDot([inst('running'), inst('stopped')]);
    expect(dot?.color).toBe('#10b981');
    expect(dot?.title).toContain('1 Hive');
  });

  it('blue + pulse when any instance is provisioning (transitional)', () => {
    const dot = deriveHiveStatusDot([inst('running'), inst('provisioning')]);
    expect(dot?.color).toBe('#3b82f6');
    expect(dot?.pulse).toBe(true);
  });

  it('red takes priority over everything when any instance errored', () => {
    const dot = deriveHiveStatusDot([inst('running'), inst('provisioning'), inst('error')]);
    expect(dot?.color).toBe('#ef4444');
  });
});
