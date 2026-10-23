/**
 * Tests for terminalLocation — where a new terminal opens, per chat tab
 * (docs/design/terminal-locations.md).
 *
 * The working-repo read is mocked at the service boundary; the store is real.
 * Properties: default = repo when the chat has one else SwarmWS; a choice is per
 * chat tab and persisted; `repo` chosen without a repo reads as SwarmWS; a custom
 * folder joins the recents (deduped, newest 6); ⌘⇧` toggles SwarmWS ↔ repo; a late
 * answer for a chat no longer in front does not change what is shown.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

const workingRepoMock = vi.fn(async (_sid: string): Promise<{ path: string; name: string } | null> => null);
vi.mock('../services/canvasSession', () => ({
  canvasSessionService: { workingRepo: (sid: string) => workingRepoMock(sid) },
}));

import { terminalLocation } from './terminalLocation';

const REPO = { path: '/r/swarmai', name: 'swarmai' };

async function front(tab: string, sid: string | null) {
  terminalLocation.setActiveChat(tab, sid);
  await terminalLocation.refresh();
}

describe('terminalLocation', () => {
  beforeEach(() => {
    terminalLocation.reset();
    workingRepoMock.mockReset();
    workingRepoMock.mockImplementation(async (sid) => (sid === 's-repo' ? REPO : null));
  });

  it('defaults to the working repo when the chat has one, else SwarmWS', async () => {
    expect(terminalLocation.snapshot().current.kind).toBe('swarmws');
    await front('t1', 's-repo');
    expect(terminalLocation.snapshot().current).toMatchObject({ kind: 'repo', key: REPO.path, cwd: REPO.path, label: 'swarmai' });
    await front('t2', 's-none');
    expect(terminalLocation.snapshot().current).toMatchObject({ kind: 'swarmws', label: 'SwarmWS' });
    expect(terminalLocation.snapshot().current.cwd).toBeUndefined();
    expect(terminalLocation.snapshot().workingRepo).toBeNull();
  });

  it('a choice is per chat tab and survives a reload (localStorage)', async () => {
    await front('t1', 's-repo');
    terminalLocation.choose({ kind: 'swarmws' });
    await front('t2', 's-repo');
    expect(terminalLocation.snapshot().current.kind).toBe('repo');
    terminalLocation.load();                       // what a restart reads back
    await front('t1', 's-repo');
    expect(terminalLocation.snapshot().current.kind).toBe('swarmws');
  });

  it('`repo` chosen while the chat has none reads as SwarmWS', async () => {
    await front('t1', 's-none');
    terminalLocation.choose({ kind: 'repo' });
    expect(terminalLocation.snapshot().current.kind).toBe('swarmws');
  });

  it('custom folders join the recents, deduped, newest first, at most 6', async () => {
    await front('t1', null);
    for (const p of ['/a', '/b', '/c', '/d', '/e', '/f', '/g', '/b/']) terminalLocation.choose({ kind: 'custom', path: p });
    expect(terminalLocation.snapshot().recents).toEqual(['/b', '/g', '/f', '/e', '/d', '/c']);
    expect(terminalLocation.snapshot().current).toMatchObject({ kind: 'custom', key: '/b', label: 'b' });
  });

  it('⌘⇧` toggles SwarmWS ↔ repo, and does nothing without a repo', async () => {
    await front('t1', 's-repo');
    expect(terminalLocation.toggleRepo()).toBe(true);
    expect(terminalLocation.snapshot().current.kind).toBe('swarmws');
    expect(terminalLocation.toggleRepo()).toBe(true);
    expect(terminalLocation.snapshot().current.kind).toBe('repo');
    await front('t2', 's-none');
    expect(terminalLocation.toggleRepo()).toBe(false);
    expect(terminalLocation.snapshot().current.kind).toBe('swarmws');
  });

  it('a late answer for a chat no longer in front does not change what is shown', async () => {
    let resolve!: (v: typeof REPO) => void;
    workingRepoMock.mockImplementationOnce(() => new Promise((r) => { resolve = r; }));
    terminalLocation.setActiveChat('t1', 's-slow');
    const pending = terminalLocation.refresh();
    await front('t2', 's-none');
    resolve(REPO);
    await pending;
    expect(terminalLocation.snapshot().current.kind).toBe('swarmws');
  });

  it('a refresh during an in-flight read reads once more after it (a run created meanwhile is seen)', async () => {
    const answers: Array<(v: typeof REPO | null) => void> = [];
    workingRepoMock.mockImplementation(() => new Promise((r) => { answers.push(r); }));
    terminalLocation.setActiveChat('t1', 's-x');      // read #1 starts (before the run)
    const p = terminalLocation.refresh();              // turn end: coalesced, marks dirty
    answers[0](null);
    await vi.waitFor(() => expect(answers).toHaveLength(2));
    answers[1](REPO);
    await p;
    expect(terminalLocation.snapshot().current.kind).toBe('repo');
    expect(workingRepoMock).toHaveBeenCalledTimes(2);
  });

  it('the generation changes only when the chat in front changes', async () => {
    await front('t1', 's-repo');
    const g = terminalLocation.generation();
    await front('t1', 's-repo');
    expect(terminalLocation.generation()).toBe(g);
    await front('t2', 's-none');
    expect(terminalLocation.generation()).not.toBe(g);
  });

  it('the snapshot is stable until something visible changes (useSyncExternalStore)', async () => {
    await front('t1', 's-repo');
    const a = terminalLocation.snapshot();
    await terminalLocation.refresh();               // same answer
    expect(terminalLocation.snapshot()).toBe(a);
  });

  it('ignores corrupt persisted state', () => {
    localStorage.setItem('terminalLocationByChatTab', JSON.stringify({ t1: { kind: 'custom' }, t2: 'x' }));
    localStorage.setItem('terminalRecentDirs', '{oops');
    terminalLocation.load();
    expect(terminalLocation.snapshot().current.kind).toBe('swarmws');
    expect(terminalLocation.snapshot().recents).toEqual([]);
  });
});
