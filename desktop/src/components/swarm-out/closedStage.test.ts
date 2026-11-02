/**
 * The run states after design 2026-11-02-swarm-out-hive-deaths-diagnostics-states §3.
 *
 * Questions:
 *  - S1: is a closed run (one stage for discarded, reverted, expired, Hive gone) done,
 *    muted, and named by its reason, each reason with its own words?
 *  - S6: does a Hive that is updating get its own label, apart from one that did not answer?
 *  - S7: is the never-produced why.paused gone from every locale?
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { beforeAll, describe, expect, it } from 'vitest';
import i18n from '../../i18n';
import type { SwarmOutRun } from '../../services/swarmOut';
import { CLOSED_WHYS, DONE_STAGES, STAGE_TONE, stageLabel } from './runReview';
import { stateLabel } from './live';

beforeAll(async () => { await i18n.changeLanguage('en'); });

const run = (over: Partial<SwarmOutRun>): SwarmOutRun => ({
  dispatch_id: 'so_x', hive_id: 'h1', hive: 'explorer', session_id: 's1', title: 'task', origin: 'swarm-out',
  sent_at: '2026-11-02T08:00:00Z', state: 'stopped', state_reason: 'idle', ...over,
});

const LOCALES = resolve(__dirname, '../../i18n/locales');
const locale = (name: string) => JSON.parse(readFileSync(resolve(LOCALES, name), 'utf8')) as Record<string, unknown>;
const at = (o: unknown, path: string[]): unknown =>
  path.reduce<unknown>((x, k) => (x && typeof x === 'object' ? (x as Record<string, unknown>)[k] : undefined), o);

describe('closed stage', () => {
  it('closed runs are done, muted and named by their reason', () => {
    expect(DONE_STAGES).toContain('closed');
    expect(DONE_STAGES).not.toContain('discarded' as never);
    expect(DONE_STAGES).not.toContain('expired' as never);
    expect(STAGE_TONE.closed).toBe('var(--color-text-muted)');

    const labels = (['discarded', 'reverted', 'expired', 'hive_gone'] as const).map((w) => stageLabel('closed', w));
    expect(CLOSED_WHYS).toEqual(['discarded', 'reverted', 'expired', 'hive_gone']);
    expect(new Set(labels).size).toBe(4);
    for (const l of labels) {
      expect(l).not.toMatch(/^swarmOut\./);          // a real text, not a missing key
      expect(l).not.toBe(stageLabel('closed'));      // the reason, not the bare stage
    }
    expect(labels).toEqual(['Discarded', 'Reverted', 'Expired', 'Hive gone']);
    // a reason this build does not know reads as the stage itself
    expect(stageLabel('closed', 'something_new')).toBe('Closed');
    expect(stageLabel('closed', null)).toBe('Closed');
    // the other stages keep their own words
    expect(stageLabel('accepted')).toBe('Accepted');
  });

  it('an updating Hive has its own label', () => {
    const updating = stateLabel(run({ state: 'unknown', state_reason: 'hive_updating' }));
    const silent = stateLabel(run({ state: 'unknown', state_reason: 'hive_silent' }));
    expect(updating).not.toBe(silent);
    expect(updating).not.toMatch(/^swarmOut\./);
    expect(updating).toBe(i18n.t('swarmOut.state.unknownUpdating'));
    expect(silent).toBe(i18n.t('swarmOut.state.unknown'));
  });

  it('no locale has why.paused', () => {
    for (const name of ['en.json', 'zh.json']) {
      const d = locale(name);
      expect(at(d, ['swarmOut', 'review', 'why'])).toBeTruthy();          // the group is there
      expect(at(d, ['swarmOut', 'review', 'why', 'paused'])).toBeUndefined();
      // and the closed names are there in every locale
      for (const w of ['discarded', 'reverted', 'expired', 'hive_gone']) expect(at(d, ['swarmOut', 'review', 'closed', w])).toBeTruthy();
      expect(at(d, ['swarmOut', 'review', 'stage', 'closed'])).toBeTruthy();
      expect(at(d, ['swarmOut', 'state', 'unknownUpdating'])).toBeTruthy();
    }
  });
});
