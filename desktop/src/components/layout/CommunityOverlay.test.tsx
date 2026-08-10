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
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, cleanup, waitFor, fireEvent, within } from '@testing-library/react';
import { CommunityContent } from './CommunityOverlay';

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

  it('surfaces an error when a mutation rejects (does not silently swallow)', async () => {
    fetchFeed.mockResolvedValue([]);
    fetchSources.mockResolvedValue(oneSource);
    updateSource.mockRejectedValue(new Error('409'));
    setup();
    fireEvent.click(screen.getByTestId('community-tab-watching'));
    fireEvent.click(await screen.findByTestId('source-toggle'));
    await waitFor(() => expect(screen.getByText(/Couldn't update/i)).toBeTruthy());
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

  it('renders the engagement LIST with a clickable GitHub comment link', async () => {
    fetchFeed.mockResolvedValue([]);
    fetchEngagement.mockResolvedValue(engWith([item()]));
    setup();
    fireEvent.click(screen.getByTestId('community-tab-outbound'));
    const row = await screen.findByTestId('community-engagement-row');
    expect(row.textContent).toContain('a/b #1');
    expect(row.textContent).toContain('T-MEM');
    // The row's GitHub-open control carries the real comment URL.
    const opener = within(row).getByTestId('engagement-open-github');
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null);
    fireEvent.click(opener);
    expect(openSpy).toHaveBeenCalledWith('https://github.com/a/b/issues/1#c1', '_blank', 'noopener,noreferrer');
    openSpy.mockRestore();
  });

  it('surfaces needs-followup rows FIRST and expands replies on click', async () => {
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
    const rows = await screen.findAllByTestId('community-engagement-row');
    // needs-followup (a/b) sorts before posted-only (c/d)
    expect(rows[0].textContent).toContain('a/b #1');
    expect(rows[1].textContent).toContain('c/d #2');
    // reply body hidden until toggled
    expect(within(rows[0]).queryByText(/merged, thanks/)).toBeNull();
    fireEvent.click(within(rows[0]).getByTestId('engagement-toggle-replies'));
    await waitFor(() => expect(within(rows[0]).getByText(/merged, thanks/)).toBeTruthy());
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

  it('discloses the list cap when KPI total exceeds shown rows (no silent truncation)', async () => {
    fetchFeed.mockResolvedValue([]);
    // 216 posted total, but only 2 items in the list → must say "showing 2 of 216"
    fetchEngagement.mockResolvedValue(engWith(
      [item({ repo: 'a/b', issueNumber: 1 }), item({ repo: 'c/d', issueNumber: 2 })],
      { commentsPosted: 216, repliesReceived: 100, maintainerReplies: 5, stars: null },
    ));
    setup();
    fireEvent.click(screen.getByTestId('community-tab-outbound'));
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
    await screen.findByTestId('community-engagement-row');
    expect(screen.queryByTestId('community-list-cap')).toBeNull();
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

    // The mock factory body: everything between `communityService: {` and its closing.
    const factory = self.slice(self.indexOf('vi.mock('), self.indexOf('}));'));
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
