/**
 * Tests for sessionBinding — the pure guard that stops a fresh/active tab from
 * binding to (or backfilling) a session already owned by ANOTHER tab.
 *
 * Root regression under test (run_3e0672d2, perpetual-thinking dispatch bug):
 * an overlay dispatch opens a new tab while the previously-active tab is still
 * streaming session S; the new empty tab must NOT inherit S. Encoded as: when
 * another tab already owns S, canBindSessionToActiveTab(S, newTab) === false.
 */
import { describe, it, expect } from 'vitest';
import { sessionOwnedByOtherTab, canBindSessionToActiveTab } from '../sessionBinding';

const tabs = (list: Array<[string, string | undefined]>) =>
  list.map(([id, sessionId]) => ({ id, sessionId }));

describe('sessionOwnedByOtherTab', () => {
  it('TRUE when another tab already holds this session (the bug scenario)', () => {
    // prev tab "t1" is streaming session S; fresh dispatch tab "t2" just became active
    const t = tabs([['t1', 'S'], ['t2', undefined]]);
    expect(sessionOwnedByOtherTab('S', 't2', t)).toBe(true);
  });

  it('FALSE when the session belongs to the active tab itself', () => {
    const t = tabs([['t1', 'S'], ['t2', undefined]]);
    expect(sessionOwnedByOtherTab('S', 't1', t)).toBe(false);
  });

  it('FALSE when no other tab holds the session (genuinely new session for this tab)', () => {
    const t = tabs([['t1', undefined], ['t2', 'NEW']]);
    expect(sessionOwnedByOtherTab('NEW', 't2', t)).toBe(false);
  });

  it('FALSE for empty session / missing active id (nothing to bind)', () => {
    const t = tabs([['t1', 'S']]);
    expect(sessionOwnedByOtherTab(undefined, 't1', t)).toBe(false);
    expect(sessionOwnedByOtherTab('S', undefined, t)).toBe(false);
  });
});

describe('canBindSessionToActiveTab', () => {
  it('BLOCKS binding when the session is owned by another (streaming) tab — the fix', () => {
    const t = tabs([['t1', 'S'], ['t2', undefined]]);
    // t2 is the fresh dispatch tab; S is t1's live session → must NOT bind
    expect(canBindSessionToActiveTab('S', 't2', t)).toBe(false);
  });

  it('ALLOWS binding a session freshly created for the active tab', () => {
    // session_start wrote S onto t2's ref; no other tab has S → safe to mirror to global/backfill
    const t = tabs([['t1', undefined], ['t2', 'S']]);
    expect(canBindSessionToActiveTab('S', 't2', t)).toBe(true);
  });

  it('ALLOWS binding when the active tab is the sole tab with this new session', () => {
    const t = tabs([['t1', 'S']]);
    expect(canBindSessionToActiveTab('S', 't1', t)).toBe(true);
  });

  it('FALSE when there is no session or no active tab', () => {
    const t = tabs([['t1', 'S']]);
    expect(canBindSessionToActiveTab(undefined, 't1', t)).toBe(false);
    expect(canBindSessionToActiveTab('S', undefined, t)).toBe(false);
  });
});
