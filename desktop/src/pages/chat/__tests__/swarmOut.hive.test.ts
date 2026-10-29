/**
 * The Hive tab's identity and approval rule (pages/chat/swarmOut.ts; design
 * docs/design/2026-10-29-swarm-out-hive-session-tab.md §4, §6.4).
 *
 * Questions: is a Hive tab told apart from Swarm Out and the user's tabs, and kept out
 * of normalTabs; which commands may be approved in a Hive tab.
 */
import { describe, it, expect } from 'vitest';
import {
  HIVE_TAB_PREFIX, MAX_APPROVABLE_COMMAND_CHARS, SWARM_OUT_TAB_ID, hiveCommandApprovable, hiveTabId, hiveTabOf,
  isHiveTab, isSwarmOutTab, normalTabs,
} from '../swarmOut';

describe('Hive tab identity', () => {
  it('names a tab per session, distinct from Swarm Out and from a user tab', () => {
    const id = hiveTabId('sess-1');
    expect(id).toBe(`${HIVE_TAB_PREFIX}sess-1`);
    expect(isHiveTab(id)).toBe(true);
    expect(isSwarmOutTab(id)).toBe(false);
    expect(isHiveTab(SWARM_OUT_TAB_ID)).toBe(false);
    expect(isHiveTab('tab-123')).toBe(false);
    expect(isHiveTab(undefined)).toBe(false);
    expect(hiveTabId('a')).not.toBe(hiveTabId('b'));
  });

  it('normalTabs leaves out both Swarm Out and the Hive tab; hiveTabOf finds the Hive tab', () => {
    const tabs = [{ id: 'a' }, { id: SWARM_OUT_TAB_ID }, { id: hiveTabId('s') }, { id: 'b' }];
    expect(normalTabs(tabs).map((t) => t.id)).toEqual(['a', 'b']);
    expect(hiveTabOf(tabs)?.id).toBe(hiveTabId('s'));
    expect(hiveTabOf([{ id: 'a' }])).toBeUndefined();
  });
});

describe('hiveCommandApprovable', () => {
  it('approves an ordinary command, with tabs and newlines', () => {
    expect(hiveCommandApprovable('ls -la\n\tgit status\r\n')).toBe(true);
    expect(hiveCommandApprovable('x'.repeat(MAX_APPROVABLE_COMMAND_CHARS))).toBe(true);
  });

  it('refuses one over the limit', () => {
    expect(hiveCommandApprovable('x'.repeat(MAX_APPROVABLE_COMMAND_CHARS + 1))).toBe(false);
  });

  it.each([
    ['NUL', 'rm\u0000 -rf /'], ['ESC', 'echo \u001b[2Jhi'], ['DEL', 'a\u007fb'], ['C1', 'a\u0085b'],
    ['RLO', 'echo \u202Etxt.exe'], ['RLM', 'a\u200Fb'], ['LRM', 'a\u200Eb'], ['ALM', 'a\u061Cb'],
    ['ZWSP', 'a\u200Bb'], ['ZWJ', 'a\u200Db'], ['BOM', '\uFEFFls'], ['tag', 'ls\u{E0041}'],
    ['line separator', 'a\u2028b'], ['paragraph separator', 'a\u2029b'], ['vertical tab', 'a\u000Bb'], ['LRI', 'a⁦b'], ['PDI', 'a⁩b'], ['LRE', 'a‪b'],
  ])('refuses a command with %s', (_name, cmd) => {
    expect(hiveCommandApprovable(cmd)).toBe(false);
  });
});
