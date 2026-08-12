/**
 * CommunityOverlay — 3-tab read-only surface (run_5165013e).
 *
 * Invariants under test:
 *   • all 3 tabs render + switch (Feed / Sources / Engagement);
 *   • each tab has loading / error / empty branches (the 5-overlay fetch pattern —
 *     a failed OR empty fetch never renders a permanent spinner or false-zero);
 *   • Feed item click closes the overlay + dispatches swarm:open-file (Canvas);
 *   • Engagement shows only data-backed metrics — NO fabricated quality score;
 *   • Sources rows render managed_by (the self_tune-coexistence field).
 *
 * The community service is mocked at the boundary (system boundary = HTTP).
 */
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, screen, cleanup, waitFor, fireEvent, within } from '@testing-library/react';
import { CommunityContent } from './CommunityOverlay';

// openExternal is the Tauri system-browser helper. External links (Outbound comment,
// Hot Topics thread) route through it — a raw window.open is dead in the WKWebview.
// Mock at the module boundary so we can assert the URL it's called with.
// openExternal is async (Promise<void>) — the component now attaches .catch(), so the
// mock MUST return a promise (a bare undefined would throw "Cannot read .catch").
const openExternal = vi.fn((_url: string) => Promise.resolve());
vi.mock('../../utils/openExternal', () => ({
  openExternal: (url: string) => openExternal(url),
}));

const fetchFeed = vi.fn();
const fetchSources = vi.fn();
const fetchEngagement = vi.fn();
const addSource = vi.fn();
const updateSource = vi.fn();
const deleteSource = vi.fn();
const addMember = vi.fn();
const deleteMember = vi.fn();
const fetchHotTopics = vi.fn();
// A WHOLE-SERVICE mock must list EVERY method the component calls: a missing key makes
// the component's `useFetch(communityService.x)` receive `undefined` and throw a
// TypeError, which surfaced as 22 unhandled errors + a NON-ZERO vitest exit while all
// 22 assertions still reported "passed" (fetchHotTopics was added to the overlay but
// not here). The contract test below pins this so the next added method can't repeat it.
vi.mock('../../services/community', () => ({
  communityService: {
    fetchFeed: () => fetchFeed(),
    fetchSources: () => fetchSources(),
    fetchEngagement: () => fetchEngagement(),
    fetchHotTopics: () => fetchHotTopics(),
    addSource: (f: unknown) => addSource(f),
    updateSource: (id: string, p: unknown) => updateSource(id, p),
    deleteSource: (id: string) => deleteSource(id),
    addMember: (id: string, v: string) => addMember(id, v),
    deleteMember: (id: string, v: string) => deleteMember(id, v),
  },
}));

// fetchFeed now returns { items, count, truncated } (not a bare array) so the UI can
// surface the honest cap. Wrap fixture items in that shape in one place.
function feedOf(items: unknown[], over: Record<string, unknown> = {}) {
  return { items, count: items.length, truncated: false, ...over };
}

// A feed WITH editable members (rss → urls). Helper keeps the new member fields in
// one place so fixtures don't drift from the CommunitySource shape.
function srcWithMembers(over: Record<string, unknown> = {}) {
  return {
    id: 'ai-eng', name: 'AI Engineering', type: 'rss', tier: 'engineering', enabled: true,
    managedBy: 'manual',
    members: ['https://a.com/feed', 'https://b.com/feed'], memberCount: 2,
    membersTruncated: false, memberKind: 'urls', tags: [],
    ...over,
  };
}

// HotTopicsSection fetches on Inbound mount; default it to empty so tests that don't
// care about hot topics don't hit an undefined return. Individual hot-topics tests
// override this.
beforeEach(() => {
  fetchHotTopics.mockResolvedValue({ scannedAt: null, topics: [] });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function setup(close = vi.fn()) {
  render(<CommunityContent close={close} />);
  return close;
}

describe('CommunityOverlay — tabs', () => {
  it('renders all three tabs (Inbound/Watching/Outbound) and defaults to Inbound', async () => {
    fetchFeed.mockResolvedValue([]);
    setup();
    expect(screen.getByTestId('community-tab-inbound')).toBeTruthy();
    expect(screen.getByTestId('community-tab-watching')).toBeTruthy();
    expect(screen.getByTestId('community-tab-outbound')).toBeTruthy();
    // Inbound fetch fires on mount (default tab)
    await waitFor(() => expect(fetchFeed).toHaveBeenCalled());
  });

  it('switches to Watching tab and fetches sources', async () => {
    fetchFeed.mockResolvedValue([]);
    fetchSources.mockResolvedValue([]);
    setup();
    fireEvent.click(screen.getByTestId('community-tab-watching'));
    await waitFor(() => expect(fetchSources).toHaveBeenCalled());
  });

  it('marks the clicked tab active and the previous one inactive', async () => {
    fetchFeed.mockResolvedValue([]);
    fetchSources.mockResolvedValue([]);
    setup();
    const inboundTab = screen.getByTestId('community-tab-inbound');
    const watchingTab = screen.getByTestId('community-tab-watching');
    // Inbound active on mount (underline via panel-accent border class)
    expect(inboundTab.className).toContain('border-[var(--panel-accent');
    fireEvent.click(watchingTab);
    await waitFor(() => {
      expect(watchingTab.className).toContain('border-[var(--panel-accent');
      expect(inboundTab.className).not.toContain('border-[var(--panel-accent');
    });
  });
});

describe('CommunityOverlay — Inbound tab', () => {
  it('shows empty state when no signals', async () => {
    fetchFeed.mockResolvedValue([]);
    setup();
    await waitFor(() => expect(screen.getByText(/No recent signals/i)).toBeTruthy());
  });

  it('shows error state when fetch rejects', async () => {
    fetchFeed.mockRejectedValue(new Error('boom'));
    setup();
    await waitFor(() => expect(screen.getByText(/Couldn't load/i)).toBeTruthy());
  });

  it('separates the latest Report into a card, keeping Signals in the daily flow', async () => {
    fetchFeed.mockResolvedValue(feedOf([
      { path: 'Knowledge/Reports/2026-08-09-weekly.html', category: 'Reports', name: '2026-08-09-weekly.html', mtime: 300 },
      { path: 'Knowledge/Signals/2026-08-07-digest.md', category: 'Signals', name: '2026-08-07-digest.md', mtime: 200 },
    ]));
    setup();
    // Report → its own card; Signal → the feed list. They are NOT in one flat list.
    const card = await screen.findByTestId('community-report-card');
    expect(card.textContent).toContain('2026-08-09-weekly.html');
    const item = await screen.findByTestId('community-feed-item');
    expect(item.textContent).toContain('2026-08-07-digest.md');
    // The report is NOT also rendered as a feed item (no interleaving).
    expect(item.textContent).not.toContain('weekly.html');
  });

  it('renders signal items and opens a file on click (closes overlay + dispatches)', async () => {
    fetchFeed.mockResolvedValue(feedOf([
      { path: 'Knowledge/Signals/2026-08-07-digest.md', category: 'Signals', name: '2026-08-07-digest.md', mtime: 100 },
    ]));
    const close = vi.fn();
    const handler = vi.fn();
    document.addEventListener('swarm:open-file', handler);
    setup(close);
    const item = await screen.findByTestId('community-feed-item');
    fireEvent.click(item);
    expect(close).toHaveBeenCalled();
    expect(handler).toHaveBeenCalled();
    const ev = handler.mock.calls[0][0] as CustomEvent;
    expect(ev.detail.path).toBe('Knowledge/Signals/2026-08-07-digest.md');
    // Order contract (BrainHub precedent): close BEFORE dispatch, else Canvas
    // renders UNDER the overlay host.
    expect(close.mock.invocationCallOrder[0]).toBeLessThan(handler.mock.invocationCallOrder[0]);
    document.removeEventListener('swarm:open-file', handler);
  });

  it('surfaces the honest cap when the feed is truncated (no silent partial list)', async () => {
    fetchFeed.mockResolvedValue(feedOf(
      [{ path: 'Knowledge/Signals/a.md', category: 'Signals', name: 'a.md', mtime: 100 }],
      { count: 100, truncated: true },
    ));
    setup();
    const note = await screen.findByTestId('community-feed-truncated');
    expect(note.textContent).toMatch(/more on disk/i);
    // Must disclose the TRUE fetched count (100 = whole-feed cap), NOT signals.length
    // (which is 1 here). Guards the adversarial-review finding that the note misstated
    // the number. Also assert it is NOT the misleading signal-subset count.
    expect(note.textContent).toContain('100');
    expect(note.textContent).not.toMatch(/newest 1 /);
  });

  it('does NOT show the cap note when the feed is complete', async () => {
    fetchFeed.mockResolvedValue(feedOf([
      { path: 'Knowledge/Signals/a.md', category: 'Signals', name: 'a.md', mtime: 100 },
    ]));
    setup();
    await screen.findByTestId('community-feed-item');
    expect(screen.queryByTestId('community-feed-truncated')).toBeNull();
  });

  it('report card click opens the report file in Canvas', async () => {
    fetchFeed.mockResolvedValue(feedOf([
      { path: 'Knowledge/Reports/2026-08-09-weekly.html', category: 'Reports', name: '2026-08-09-weekly.html', mtime: 300 },
    ]));
    const close = vi.fn();
    const handler = vi.fn();
    document.addEventListener('swarm:open-file', handler);
    setup(close);
    fireEvent.click(await screen.findByTestId('community-report-card'));
    expect(close).toHaveBeenCalled();
    const ev = handler.mock.calls[0][0] as CustomEvent;
    expect(ev.detail.path).toBe('Knowledge/Reports/2026-08-09-weekly.html');
    document.removeEventListener('swarm:open-file', handler);
  });
});

describe('CommunityOverlay — Sources tab', () => {
  it('renders source rows with managed_by (self_tune-coexistence field)', async () => {
    fetchFeed.mockResolvedValue([]);
    fetchSources.mockResolvedValue([
      srcWithMembers(),
    ]);
    setup();
    fireEvent.click(screen.getByTestId('community-tab-watching'));
    const row = await screen.findByTestId('community-source-row');
    expect(row.textContent).toContain('AI Engineering');
    expect(row.textContent).toContain('manual'); // managed_by rendered
  });

  it('on empty sources, still offers the add-source affordance (fresh user can add their first)', async () => {
    // Phase-2 behavior change: empty no longer dead-ends on a banner — it renders
    // the add form so a fresh user can add their first source.
    fetchFeed.mockResolvedValue([]);
    fetchSources.mockResolvedValue([]);
    setup();
    fireEvent.click(screen.getByTestId('community-tab-watching'));
    await waitFor(() => expect(screen.getByTestId('source-add-open')).toBeTruthy());
  });

  it('shows error state when sources fetch rejects', async () => {
    fetchFeed.mockResolvedValue([]);
    fetchSources.mockRejectedValue(new Error('boom'));
    setup();
    fireEvent.click(screen.getByTestId('community-tab-watching'));
    await waitFor(() => expect(screen.getByText(/Couldn't load/i)).toBeTruthy());
  });
});

describe('CommunityOverlay — Sources tab (editable, Phase-2)', () => {
  const oneSource = [
    srcWithMembers(),
  ];

  it('toggle fires updateSource with flipped enabled, then refetches', async () => {
    fetchFeed.mockResolvedValue([]);
    fetchSources.mockResolvedValue(oneSource);
    updateSource.mockResolvedValue(undefined);
    setup();
    fireEvent.click(screen.getByTestId('community-tab-watching'));
    const toggle = await screen.findByTestId('source-toggle');
    fireEvent.click(toggle);
    await waitFor(() => expect(updateSource).toHaveBeenCalledWith('ai-eng', { enabled: false }));
    // refetch happened (fetchSources called again after mutation)
    await waitFor(() => expect(fetchSources.mock.calls.length).toBeGreaterThanOrEqual(2));
  });

  it('changing tier fires updateSource with the new tier', async () => {
    fetchFeed.mockResolvedValue([]);
    fetchSources.mockResolvedValue(oneSource);
    updateSource.mockResolvedValue(undefined);
    setup();
    fireEvent.click(screen.getByTestId('community-tab-watching'));
    const tier = await screen.findByTestId('source-tier');
    fireEvent.change(tier, { target: { value: 'frontier' } });
    await waitFor(() => expect(updateSource).toHaveBeenCalledWith('ai-eng', { tier: 'frontier' }));
  });

  it('delete requires a SECOND confirm click before deleteSource fires', async () => {
    fetchFeed.mockResolvedValue([]);
    fetchSources.mockResolvedValue(oneSource);
    deleteSource.mockResolvedValue(undefined);
    setup();
    fireEvent.click(screen.getByTestId('community-tab-watching'));
    const del = await screen.findByTestId('source-delete');
    fireEvent.click(del); // first click → arms confirm, does NOT delete
    expect(deleteSource).not.toHaveBeenCalled();
    const confirm = await screen.findByTestId('source-delete-confirm');
    fireEvent.click(confirm); // second click → deletes
    await waitFor(() => expect(deleteSource).toHaveBeenCalledWith('ai-eng'));
  });

  it('add form submits addSource and refetches', async () => {
    fetchFeed.mockResolvedValue([]);
    fetchSources.mockResolvedValue(oneSource);
    addSource.mockResolvedValue(undefined);
    setup();
    fireEvent.click(screen.getByTestId('community-tab-watching'));
    fireEvent.click(await screen.findByTestId('source-add-open'));
    fireEvent.change(screen.getByTestId('add-id'), { target: { value: 'new-feed' } });
    fireEvent.change(screen.getByTestId('add-name'), { target: { value: 'New Feed' } });
    fireEvent.click(screen.getByTestId('add-submit'));
    await waitFor(() => expect(addSource).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'new-feed', name: 'New Feed', type: 'rss', tier: 'engineering' }),
    ));
  });

  it('the add-type dropdown offers github-people (backend FeedType with editable logins)', async () => {
    // Regression guard for the frontend-zero-wiring bug: github-people is a real
    // FeedType the backend accepts + has editable `logins` members, but was missing
    // from the type <select> so no user could create one.
    fetchFeed.mockResolvedValue([]);
    fetchSources.mockResolvedValue(oneSource);
    setup();
    fireEvent.click(screen.getByTestId('community-tab-watching'));
    fireEvent.click(await screen.findByTestId('source-add-open'));
    const sel = await screen.findByTestId('add-type');
    const values = Array.from(sel.querySelectorAll('option')).map((o) => (o as HTMLOptionElement).value);
    expect(values).toContain('github-people');
    expect(values).toContain('github-community');
  });

  it('surfaces an error when a mutation rejects (does not silently swallow)', async () => {
    fetchFeed.mockResolvedValue([]);
    fetchSources.mockResolvedValue(oneSource);
    updateSource.mockRejectedValue(new Error('409'));
    setup();
    fireEvent.click(screen.getByTestId('community-tab-watching'));
    fireEvent.click(await screen.findByTestId('source-toggle'));
    await waitFor(() => expect(screen.getByText(/Couldn't update/i)).toBeTruthy());
  });

  it('surfaces the backend 422 detail verbatim (not a generic "Try again")', async () => {
    // A validation failure (FastAPI 422 detail) is ACTIONABLE — the UI must show it,
    // not swallow it into the generic fallback. Axios puts the body on
    // error.response.data.detail.
    fetchFeed.mockResolvedValue([]);
    fetchSources.mockResolvedValue(oneSource);
    updateSource.mockRejectedValue({
      response: { data: { detail: 'Invalid url: must be an https:// URL' } },
    });
    setup();
    fireEvent.click(screen.getByTestId('community-tab-watching'));
    fireEvent.click(await screen.findByTestId('source-toggle'));
    await waitFor(() =>
      expect(screen.getByText(/must be an https:\/\/ URL/i)).toBeTruthy(),
    );
    // and NOT the generic fallback
    expect(screen.queryByText(/Try again/i)).toBeNull();
  });
});

describe('CommunityOverlay — member editing (B4)', () => {
  async function openSources(sources: unknown[]) {
    fetchFeed.mockResolvedValue([]);
    fetchSources.mockResolvedValue(sources);
    setup();
    fireEvent.click(screen.getByTestId('community-tab-watching'));
    await screen.findByTestId('community-source-row');
  }

  it('expanding a feed lists its members', async () => {
    await openSources([srcWithMembers()]);
    fireEvent.click(screen.getByTestId('source-expand'));
    const rows = await screen.findAllByTestId('member-row');
    expect(rows.length).toBe(2);
    expect(screen.getByText('https://a.com/feed')).toBeTruthy();
  });

  it('adding a member fires addMember then refetches', async () => {
    addMember.mockResolvedValue(undefined);
    await openSources([srcWithMembers()]);
    fireEvent.click(screen.getByTestId('source-expand'));
    fireEvent.change(await screen.findByTestId('member-add-input'), { target: { value: 'https://c.com/feed' } });
    fireEvent.click(screen.getByTestId('member-add-submit'));
    await waitFor(() => expect(addMember).toHaveBeenCalledWith('ai-eng', 'https://c.com/feed'));
    await waitFor(() => expect(fetchSources.mock.calls.length).toBeGreaterThanOrEqual(2));
  });

  it('deleting a member requires a SECOND confirm click', async () => {
    deleteMember.mockResolvedValue(undefined);
    await openSources([srcWithMembers()]);
    fireEvent.click(screen.getByTestId('source-expand'));
    const del = (await screen.findAllByTestId('member-delete'))[0];
    fireEvent.click(del); // arms confirm
    expect(deleteMember).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId('member-delete-confirm'));
    await waitFor(() => expect(deleteMember).toHaveBeenCalledWith('ai-eng', 'https://a.com/feed'));
  });

  it('a no-editable-member feed type has NO expand affordance', async () => {
    await openSources([srcWithMembers({ id: 'gt', name: 'GT', type: 'github-trending', memberKind: null, members: [], memberCount: 0 })]);
    expect(screen.queryByTestId('source-expand')).toBeNull();
  });

  it('shows truncation note when membersTruncated', async () => {
    await openSources([srcWithMembers({ members: ['u1', 'u2'], memberCount: 55, membersTruncated: true })]);
    fireEvent.click(screen.getByTestId('source-expand'));
    expect(await screen.findByText(/Showing first 2 of 55/i)).toBeTruthy();
  });
});

describe('CommunityOverlay — Outbound tab', () => {
  const engWith = (items: unknown[], kpis = { commentsPosted: 12, repliesReceived: 7, maintainerReplies: 3, stars: 42 }) => ({ kpis, items });
  const item = (over: Record<string, unknown> = {}) => ({
    repo: 'a/b', issueNumber: 1, topic: 'T-MEM', status: 'published',
    commentUrl: 'https://github.com/a/b/issues/1#c1', postedAt: '2026-08-01T10:00:00Z',
    confidence: 9, replyCount: 0, hasMaintainerReply: false, needsFollowup: false, replies: [],
    ...over,
  });

  it('renders a demoted KPI strip (data-backed, no fabricated quality score)', async () => {
    fetchFeed.mockResolvedValue([]);
    fetchEngagement.mockResolvedValue(engWith([]));
    setup();
    fireEvent.click(screen.getByTestId('community-tab-outbound'));
    await waitFor(() => expect(screen.getByTestId('community-kpi-strip')).toBeTruthy());
    const strip = screen.getByTestId('community-kpi-strip').textContent ?? '';
    expect(strip).toContain('12 posted');
    expect(strip).toContain('3 maintainer');
    expect((document.body.textContent ?? '').toLowerCase()).not.toContain('quality');
  });

  it('opens a posted engagement in the SYSTEM browser via openExternal (NOT window.open)', async () => {
    // The bug this fixes: EngagementRow used window.open, silently ignored by the
    // Tauri v2 WKWebview. It must call openExternal(commentUrl) instead.
    fetchFeed.mockResolvedValue([]);
    fetchEngagement.mockResolvedValue(engWith([item()]));
    setup();
    fireEvent.click(screen.getByTestId('community-tab-outbound'));
    // one posted (handled) row — expand the collapsed group first
    fireEvent.click(await screen.findByTestId('handled-toggle'));
    const row = await screen.findByTestId('engagement-posted-row');
    expect(row.textContent).toContain('a/b #1');
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null);
    fireEvent.click(row);
    expect(openExternal).toHaveBeenCalledWith('https://github.com/a/b/issues/1#c1');
    expect(openSpy).not.toHaveBeenCalled(); // window.open is dead in Tauri — must not be used
    openSpy.mockRestore();
  });

  it('surfaces needs-followup rows as the hero (with the latest reply inline) and collapses handled', async () => {
    fetchFeed.mockResolvedValue([]);
    fetchEngagement.mockResolvedValue(engWith([
      item({ repo: 'c/d', issueNumber: 2, needsFollowup: false }),
      item({
        repo: 'a/b', issueNumber: 1, needsFollowup: true, hasMaintainerReply: true, replyCount: 1,
        replies: [{ author: 'maintainer1', body: 'merged, thanks', isMaintainer: true, createdAt: '2026-08-02T10:00:00Z' }],
      }),
    ]));
    setup();
    fireEvent.click(screen.getByTestId('community-tab-outbound'));
    // needs-followup row is the hero, shown expanded with the latest reply body inline
    const followup = await screen.findByTestId('engagement-followup-row');
    expect(followup.textContent).toContain('a/b #1');
    expect(followup.textContent).toContain('maintainer1');
    expect(followup.textContent).toContain('merged, thanks');
    // posted/handled (c/d) is DEMOTED — collapsed by default (not visible until toggled)
    expect(screen.queryByTestId('engagement-posted-row')).toBeNull();
    fireEvent.click(screen.getByTestId('handled-toggle'));
    const posted = await screen.findByTestId('engagement-posted-row');
    expect(posted.textContent).toContain('c/d #2');
  });

  it('opens a needs-followup row via openExternal on click', async () => {
    fetchFeed.mockResolvedValue([]);
    fetchEngagement.mockResolvedValue(engWith([
      item({ repo: 'a/b', issueNumber: 1, needsFollowup: true, replyCount: 1,
        replies: [{ author: 'user9', body: 'ping?', isMaintainer: false, createdAt: '2026-08-02T10:00:00Z' }] }),
    ]));
    setup();
    fireEvent.click(screen.getByTestId('community-tab-outbound'));
    fireEvent.click(await screen.findByTestId('engagement-followup-row'));
    expect(openExternal).toHaveBeenCalledWith('https://github.com/a/b/issues/1#c1');
  });

  it('shows empty state when there are no engagements', async () => {
    fetchFeed.mockResolvedValue([]);
    fetchEngagement.mockResolvedValue(engWith([], { commentsPosted: 0, repliesReceived: 0, maintainerReplies: 0, stars: null }));
    setup();
    fireEvent.click(screen.getByTestId('community-tab-outbound'));
    await waitFor(() => expect(screen.getByText(/No engagements yet/i)).toBeTruthy());
  });

  it('shows error state when engagement fetch rejects', async () => {
    fetchFeed.mockResolvedValue([]);
    fetchEngagement.mockRejectedValue(new Error('boom'));
    setup();
    fireEvent.click(screen.getByTestId('community-tab-outbound'));
    await waitFor(() => expect(screen.getByText(/Couldn't load/i)).toBeTruthy());
  });

  it('discloses the list cap inside the expanded handled group (no silent truncation)', async () => {
    fetchFeed.mockResolvedValue([]);
    // 216 posted total, but only 2 items in the list → must say "2 most recent of 216"
    fetchEngagement.mockResolvedValue(engWith(
      [item({ repo: 'a/b', issueNumber: 1 }), item({ repo: 'c/d', issueNumber: 2 })],
      { commentsPosted: 216, repliesReceived: 100, maintainerReplies: 5, stars: null },
    ));
    setup();
    fireEvent.click(screen.getByTestId('community-tab-outbound'));
    // the collapsed toggle discloses the shown-of-total up front
    const toggle = await screen.findByTestId('handled-toggle');
    expect(toggle.textContent).toContain('shown of 216');
    fireEvent.click(toggle);
    await waitFor(() => expect(screen.getByTestId('community-list-cap')).toBeTruthy());
    expect(screen.getByTestId('community-list-cap').textContent).toContain('2 most recent of 216');
  });

  it('does NOT show the cap note when the list already shows every posted comment', async () => {
    fetchFeed.mockResolvedValue([]);
    fetchEngagement.mockResolvedValue(engWith(
      [item({ repo: 'a/b', issueNumber: 1 })],
      { commentsPosted: 1, repliesReceived: 0, maintainerReplies: 0, stars: null },
    ));
    setup();
    fireEvent.click(screen.getByTestId('community-tab-outbound'));
    fireEvent.click(await screen.findByTestId('handled-toggle'));
    await screen.findByTestId('engagement-posted-row');
    expect(screen.queryByTestId('community-list-cap')).toBeNull();
  });
});

describe('CommunityOverlay — Hot Topics (live signals.json feed)', () => {
  const topic = (over: Record<string, unknown> = {}) => ({
    rank: 1, id: 'HT-SKILL-ARCH', topic: 'Skill / capability architecture',
    comments: 15, threads: 18, topRepo: 'danielmiessler/Personal_AI_Infrastructure',
    topNumber: 1613, topTitle: 'Surviving upgrades',
    url: 'https://github.com/danielmiessler/Personal_AI_Infrastructure/discussions/1613',
    ...over,
  });

  it('renders ranked topics with comment count + thread count', async () => {
    fetchFeed.mockResolvedValue([]);
    fetchHotTopics.mockResolvedValue({
      scannedAt: new Date().toISOString(),
      topics: [topic(), topic({ rank: 2, id: 'HT-MEMORY', topic: 'Memory & retrieval', comments: 13, threads: 4, url: 'https://github.com/MemPalace/mempalace/discussions/759' })],
    });
    setup();
    const rows = await screen.findAllByTestId('hot-topic-row');
    expect(rows.length).toBe(2);
    expect(rows[0].textContent).toContain('Skill / capability architecture');
    expect(rows[0].textContent).toContain('18 threads');
    expect(rows[0].textContent).toContain('15');
  });

  it('opens a hot topic thread in the system browser via openExternal (discussions URL)', async () => {
    fetchFeed.mockResolvedValue([]);
    fetchHotTopics.mockResolvedValue({ scannedAt: new Date().toISOString(), topics: [topic()] });
    setup();
    const row = await screen.findByTestId('hot-topic-row');
    fireEvent.click(row);
    expect(openExternal).toHaveBeenCalledWith(
      'https://github.com/danielmiessler/Personal_AI_Infrastructure/discussions/1613',
    );
  });

  it('shows a fresh (green) freshness label for a recent scan', async () => {
    fetchFeed.mockResolvedValue([]);
    fetchHotTopics.mockResolvedValue({ scannedAt: new Date().toISOString(), topics: [topic()] });
    setup();
    const label = await screen.findByTestId('hot-topics-freshness');
    expect(label.textContent).toMatch(/synced/i);
    expect(label.className).toContain('emerald');
  });

  it('shows a scan-may-be-down (amber) label when the feed is > 21 days old', async () => {
    fetchFeed.mockResolvedValue([]);
    const old = new Date(Date.now() - 40 * 86_400_000).toISOString();
    fetchHotTopics.mockResolvedValue({ scannedAt: old, topics: [topic()] });
    setup();
    const label = await screen.findByTestId('hot-topics-freshness');
    expect(label.textContent).toMatch(/scan may be down/i);
    expect(label.className).toContain('amber');
  });

  it('renders nothing when there are no hot topics (fail-quiet, no error banner)', async () => {
    fetchFeed.mockResolvedValue([]);
    fetchHotTopics.mockResolvedValue({ scannedAt: null, topics: [] });
    setup();
    // Inbound still renders (the signals empty-state), but hot-topics section is absent
    await waitFor(() => expect(screen.getByText(/No recent signals/i)).toBeTruthy());
    expect(screen.queryByTestId('community-hot-topics')).toBeNull();
  });

  it('disables the row (no openExternal) when a topic has no url', async () => {
    fetchFeed.mockResolvedValue([]);
    fetchHotTopics.mockResolvedValue({ scannedAt: new Date().toISOString(), topics: [topic({ url: '' })] });
    setup();
    const row = await screen.findByTestId('hot-topic-row');
    expect(row).toHaveProperty('disabled', true); // the disabled logic itself is under test, not just the no-call
    fireEvent.click(row);
    expect(openExternal).not.toHaveBeenCalled();
  });

  it('shows "scan time unknown" (amber) when scannedAt is null but topics exist', async () => {
    fetchFeed.mockResolvedValue([]);
    fetchHotTopics.mockResolvedValue({ scannedAt: null, topics: [topic()] });
    setup();
    const label = await screen.findByTestId('hot-topics-freshness');
    expect(label.textContent).toMatch(/scan time unknown/i);
    expect(label.className).toContain('amber');
  });
});

// ---------------------------------------------------------------------------
// MOCK-COMPLETENESS CONTRACT (run_a1f4c2d8)
//
// A whole-service `vi.mock` is a SNAPSHOT of an interface, and nothing kept it in sync
// with the component. When fetchHotTopics was added to the overlay but not to the mock,
// `useFetch(communityService.fetchHotTopics)` got `undefined`, `fetcher()` threw a
// TypeError, and vitest reported *22 unhandled errors + a non-zero exit* while all 22
// assertions still said "passed" — i.e. a RED file that reads GREEN in the summary. CI
// runs `vitest run` over the whole suite, so this failed the frontend job silently.
//
// The source fix (try/catch in useFetch) makes such a failure degrade to the visible
// error state instead of an unhandled rejection. THIS test closes the other half: it
// scans the component for every `communityService.<method>` reference and asserts the
// mock above defines each one — so the NEXT added method fails loudly and locally,
// pointing at the missing key, instead of surfacing as unhandled-error noise.
// ---------------------------------------------------------------------------
describe('CommunityOverlay — mock completeness contract', () => {
  it('the vi.mock lists every communityService method the component calls', async () => {
    const { readFileSync } = await import('fs');
    const { join, dirname } = await import('path');
    const { fileURLToPath } = await import('url');
    const here = dirname(fileURLToPath(import.meta.url));

    const component = readFileSync(join(here, 'CommunityOverlay.tsx'), 'utf-8');
    const self = readFileSync(join(here, 'CommunityOverlay.test.tsx'), 'utf-8');

    const used = [...component.matchAll(/communityService\.([a-zA-Z0-9_]+)/g)]
      .map((m) => m[1]);
    expect(used.length, 'no communityService references found — the scan regex broke')
      .toBeGreaterThan(0);

    // The mock factory body: from the `communityService: {` marker to the next `}));`.
    // Anchor on `communityService:` (NOT the first `vi.mock(` — there are now two mock
    // calls in this file, and the openExternal one comes first).
    const csStart = self.indexOf('communityService: {');
    const factory = self.slice(csStart, self.indexOf('}));', csStart));
    const missing = [...new Set(used)].filter(
      (m) => !new RegExp(`\\b${m}\\s*:`).test(factory),
    );

    expect(
      missing,
      `The component calls these communityService methods but the vi.mock above does ` +
        `not define them. Each one is \`undefined\` at runtime → \`fetcher()\` throws ` +
        `a TypeError → unhandled errors + non-zero vitest exit, WITHOUT any assertion ` +
        `failing. Add them to the mock:\n  ${missing.join('\n  ')}`,
    ).toEqual([]);
  });
});
