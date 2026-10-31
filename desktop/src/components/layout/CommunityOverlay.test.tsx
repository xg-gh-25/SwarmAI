import type React from 'react';
/**
 * CommunityOverlay — Inbound + Watching tabs (run_5165013e).
 *
 * Invariants under test:
 *   • both tabs render + switch (Inbound / Watching); no GitHub_Community-only parts
 *     (Hot Topics, Outbound) — usable-by-anyone P7;
 *   • each tab has loading / error / empty branches (the 5-overlay fetch pattern —
 *     a failed OR empty fetch never renders a permanent spinner or false-zero);
 *   • Feed item click closes the overlay + dispatches swarm:open-file (Canvas);
 *   • Sources rows render managed_by (the self_tune-coexistence field).
 *
 * The community service is mocked at the boundary (system boundary = HTTP).
 */
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { screen, cleanup, waitFor, fireEvent } from '@testing-library/react';
import { CommunityContent } from './CommunityOverlay';
import { QueryClient as __QC, QueryClientProvider as __QCP } from '@tanstack/react-query';
import { render as __render } from '@testing-library/react';
// The overlay reads its data through TanStack Query (overlayQueries): a fresh client
// per render, so no cache carries over between tests.
const renderQ = (ui: React.ReactElement) =>
  __render(<__QCP client={new __QC({ defaultOptions: { queries: { retry: false } } })}>{ui}</__QCP>);

const fetchFeed = vi.fn();
const fetchSources = vi.fn();
const addSource = vi.fn();
const updateSource = vi.fn();
const deleteSource = vi.fn();
const addMember = vi.fn();
const deleteMember = vi.fn();
// A WHOLE-SERVICE mock must list EVERY method the component calls: a missing key makes
// the component's `useFetch(communityService.x)` receive `undefined` and throw a
// TypeError, which surfaced as 22 unhandled errors + a NON-ZERO vitest exit while all
// 22 assertions still reported "passed" (a method was added to the overlay but not
// here). The contract test below pins this so the next added method can't repeat it.
vi.mock('../../services/community', () => ({
  communityService: {
    fetchFeed: () => fetchFeed(),
    fetchSources: () => fetchSources(),
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
  renderQ(<CommunityContent close={close} />);
  return close;
}

describe('CommunityOverlay — tabs', () => {
  it('renders the Inbound and Watching tabs (no Outbound) and defaults to Inbound', async () => {
    fetchFeed.mockResolvedValue([]);
    setup();
    expect(screen.getByTestId('community-tab-inbound')).toBeTruthy();
    expect(screen.getByTestId('community-tab-watching')).toBeTruthy();
    expect(screen.queryByTestId('community-tab-outbound')).toBeNull();
    expect(screen.queryByText(/Hot Topics/i)).toBeNull();
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
    // Inbound selected on mount; a click moves the selection
    expect(inboundTab.getAttribute('aria-selected')).toBe('true');
    fireEvent.click(watchingTab);
    await waitFor(() => {
      expect(watchingTab.getAttribute('aria-selected')).toBe('true');
      expect(inboundTab.getAttribute('aria-selected')).toBe('false');
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

  it('the error state has a Retry that fetches again (OverlayState)', async () => {
    fetchFeed.mockRejectedValueOnce(new Error('boom')).mockResolvedValue(feedOf([]));
    setup();
    fireEvent.click(await screen.findByTestId('community-load-retry'));
    await waitFor(() => expect(screen.getByText(/No recent signals/i)).toBeTruthy());
    expect(fetchFeed).toHaveBeenCalledTimes(2);
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
