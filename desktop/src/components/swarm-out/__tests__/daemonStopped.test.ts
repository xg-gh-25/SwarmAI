/**
 * `daemon_stopped`: a Hive whose daemon died mid-turn records the turn's end when it
 * starts again (backend core/hive_boot; design docs/design/2026-11-04-kirocrew-borrow.md
 * §3.B1). The panel knows the ending: listed, bucketed as stopped, its tone, and its
 * label in every locale.
 */
import { describe, it, expect, beforeAll } from 'vitest';
import type { SwarmOutResult, SwarmOutRun } from '../../../services/swarmOut';
import { ENDINGS, endedLabel, gateLine, outcomeBucket } from '../live';
import { endedTone } from '../tone';
import i18n from '../../../i18n';
import en from '../../../i18n/locales/en.json';
import zh from '../../../i18n/locales/zh.json';

beforeAll(async () => { await i18n.changeLanguage('en'); });

const result = (over: Partial<SwarmOutResult> = {}): SwarmOutResult => ({
  ended: 'daemon_stopped', provisional: false, outcome_recorded: true, hive_records_outcomes: true,
  error: null, ended_at: '2026-11-04T01:00:00Z', turns: 2, older_turns_not_read: false, active_s: 60,
  tokens: null, cost_usd: null, cost_partial: true, report_chars: 0, kinds: ['code'], pipeline: null,
  dropped: 0, work: { repos: null, changes: [], files: [], files_omitted: 0,
    steps: { total: 0, failed: 0, agents: 0, by_tool: {} },
    human: { approved: 0, denied: 0, unanswered: 0, answered_questions: 0, said: 0 } },
  ...over,
} as unknown as SwarmOutResult);

const run = (res: SwarmOutResult): SwarmOutRun => ({
  dispatch_id: 'so_aaaaaaaaaaaa', hive_id: 'h1', session_id: 's1', sent_at: '2026-11-04T00:00:00Z',
  state: 'stopped', state_reason: 'idle', result: res,
} as unknown as SwarmOutRun);

describe('daemon_stopped', () => {
  it('daemon_stopped is a known ending', () => {
    expect(ENDINGS).toContain('daemon_stopped');
    expect(outcomeBucket(run(result()))).toBe('stopped');
    expect(endedTone(result())).toBe('attention');
    // final, unlike no_record: no "provisional", no "how it ended is not known"
    expect(endedLabel(result())).toEqual({ text: 'The Hive stopped while this turn ran', tone: 'attention' });
    for (const locale of [en, zh] as Record<string, any>[]) {
      expect(locale.swarmOut.ended.daemon_stopped).toEqual(expect.any(String));
      expect(locale.swarmOut.review.why.daemon_stopped).toEqual(expect.any(String));
    }
  });

  it('a refusal count the stopped turn lost is said, not shown as none', () => {
    const agent = { project_agent: { brain: 'B', mode: 'code-change', profile: null, delivery: null } };
    expect(gateLine(result({ ...agent, gate_refusals: {}, gate_refusals_partial: true })))
      .toBe("Brain gate: the refusals of a turn cut off by the Hive's stop are not known");
    expect(gateLine(result({ ...agent, gate_refusals: { no_push: 2 }, gate_refusals_partial: true })))
      .toBe("Brain gate refused 2 times (no_push 2) · a turn cut off by the Hive's stop is not counted");
    expect(gateLine(result({ ...agent, gate_refusals: {}, gate_refusals_partial: false }))).toBeNull();
  });
});
