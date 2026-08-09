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
import { render, screen, cleanup, waitFor, fireEvent } from '@testing-library/react';
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
  it('renders all three tabs and defaults to Feed', async () => {
    fetchFeed.mockResolvedValue([]);
    setup();
    expect(screen.getByTestId('community-tab-feed')).toBeTruthy();
    expect(screen.getByTestId('community-tab-sources')).toBeTruthy();
    expect(screen.getByTestId('community-tab-engagement')).toBeTruthy();
    // Feed fetch fires on mount (default tab)
    await waitFor(() => expect(fetchFeed).toHaveBeenCalled());
  });

  it('switches to Sources tab and fetches sources', async () => {
    fetchFeed.mockResolvedValue([]);
    fetchSources.mockResolvedValue([]);
    setup();
    fireEvent.click(screen.getByTestId('community-tab-sources'));
    await waitFor(() => expect(fetchSources).toHaveBeenCalled());
  });

  it('marks the clicked tab active and the previous one inactive', async () => {
    fetchFeed.mockResolvedValue([]);
    fetchSources.mockResolvedValue([]);
    setup();
    const feedTab = screen.getByTestId('community-tab-feed');
    const sourcesTab = screen.getByTestId('community-tab-sources');
    // Feed active on mount (underline via panel-accent border class)
    expect(feedTab.className).toContain('border-[var(--panel-accent');
    fireEvent.click(sourcesTab);
    await waitFor(() => {
      expect(sourcesTab.className).toContain('border-[var(--panel-accent');
      expect(feedTab.className).not.toContain('border-[var(--panel-accent');
    });
  });
});

describe('CommunityOverlay — Feed tab', () => {
  it('shows empty state when no items', async () => {
    fetchFeed.mockResolvedValue([]);
    setup();
    await waitFor(() => expect(screen.getByText(/No recent signals or reports/i)).toBeTruthy());
  });

  it('shows error state when fetch rejects', async () => {
    fetchFeed.mockRejectedValue(new Error('boom'));
    setup();
    await waitFor(() => expect(screen.getByText(/Couldn't load/i)).toBeTruthy());
  });

  it('renders feed items and opens a file on click (closes overlay + dispatches)', async () => {
    fetchFeed.mockResolvedValue([
      { path: 'Knowledge/Signals/2026-08-07-digest.md', category: 'Signals', name: '2026-08-07-digest.md', mtime: 100 },
    ]);
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
});

describe('CommunityOverlay — Sources tab', () => {
  it('renders source rows with managed_by (self_tune-coexistence field)', async () => {
    fetchFeed.mockResolvedValue([]);
    fetchSources.mockResolvedValue([
      srcWithMembers(),
    ]);
    setup();
    fireEvent.click(screen.getByTestId('community-tab-sources'));
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
    fireEvent.click(screen.getByTestId('community-tab-sources'));
    await waitFor(() => expect(screen.getByTestId('source-add-open')).toBeTruthy());
  });

  it('shows error state when sources fetch rejects', async () => {
    fetchFeed.mockResolvedValue([]);
    fetchSources.mockRejectedValue(new Error('boom'));
    setup();
    fireEvent.click(screen.getByTestId('community-tab-sources'));
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
    fireEvent.click(screen.getByTestId('community-tab-sources'));
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
    fireEvent.click(screen.getByTestId('community-tab-sources'));
    const tier = await screen.findByTestId('source-tier');
    fireEvent.change(tier, { target: { value: 'frontier' } });
    await waitFor(() => expect(updateSource).toHaveBeenCalledWith('ai-eng', { tier: 'frontier' }));
  });

  it('delete requires a SECOND confirm click before deleteSource fires', async () => {
    fetchFeed.mockResolvedValue([]);
    fetchSources.mockResolvedValue(oneSource);
    deleteSource.mockResolvedValue(undefined);
    setup();
    fireEvent.click(screen.getByTestId('community-tab-sources'));
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
    fireEvent.click(screen.getByTestId('community-tab-sources'));
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
    fireEvent.click(screen.getByTestId('community-tab-sources'));
    fireEvent.click(await screen.findByTestId('source-toggle'));
    await waitFor(() => expect(screen.getByText(/Couldn't update/i)).toBeTruthy());
  });
});

describe('CommunityOverlay — member editing (B4)', () => {
  async function openSources(sources: unknown[]) {
    fetchFeed.mockResolvedValue([]);
    fetchSources.mockResolvedValue(sources);
    setup();
    fireEvent.click(screen.getByTestId('community-tab-sources'));
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

describe('CommunityOverlay — Engagement tab', () => {
  it('renders data-backed KPIs and NO fabricated quality score', async () => {
    fetchFeed.mockResolvedValue([]);
    fetchEngagement.mockResolvedValue({
      commentsPosted: 12, repliesReceived: 7, maintainerReplies: 3, stars: 42,
    });
    setup();
    fireEvent.click(screen.getByTestId('community-tab-engagement'));
    await waitFor(() => expect(screen.getAllByTestId('community-kpi').length).toBeGreaterThan(0));
    const body = document.body.textContent ?? '';
    expect(body).toContain('comments posted');
    expect(body).toContain('maintainer replies');
    // No fabricated quality metric anywhere.
    expect(body.toLowerCase()).not.toContain('quality');
  });

  it('omits the stars KPI when stars is null', async () => {
    fetchFeed.mockResolvedValue([]);
    fetchEngagement.mockResolvedValue({
      commentsPosted: 5, repliesReceived: 2, maintainerReplies: 1, stars: null,
    });
    setup();
    fireEvent.click(screen.getByTestId('community-tab-engagement'));
    await waitFor(() => expect(screen.getAllByTestId('community-kpi').length).toBe(3));
  });

  it('shows error state when engagement fetch rejects', async () => {
    fetchFeed.mockResolvedValue([]);
    fetchEngagement.mockRejectedValue(new Error('boom'));
    setup();
    fireEvent.click(screen.getByTestId('community-tab-engagement'));
    await waitFor(() => expect(screen.getByText(/Couldn't load/i)).toBeTruthy());
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
