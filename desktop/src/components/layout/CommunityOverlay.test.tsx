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
vi.mock('../../services/community', () => ({
  communityService: {
    fetchFeed: () => fetchFeed(),
    fetchSources: () => fetchSources(),
    fetchEngagement: () => fetchEngagement(),
  },
}));

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
      { id: 'ai-eng', name: 'AI Engineering', type: 'rss', tier: 'engineering', enabled: true, managedBy: 'manual', sourceCount: 4, tags: [] },
    ]);
    setup();
    fireEvent.click(screen.getByTestId('community-tab-sources'));
    const row = await screen.findByTestId('community-source-row');
    expect(row.textContent).toContain('AI Engineering');
    expect(row.textContent).toContain('manual'); // managed_by rendered
  });

  it('shows empty state when no sources', async () => {
    fetchFeed.mockResolvedValue([]);
    fetchSources.mockResolvedValue([]);
    setup();
    fireEvent.click(screen.getByTestId('community-tab-sources'));
    await waitFor(() => expect(screen.getByText(/No subscribed sources/i)).toBeTruthy());
  });

  it('shows error state when sources fetch rejects', async () => {
    fetchFeed.mockResolvedValue([]);
    fetchSources.mockRejectedValue(new Error('boom'));
    setup();
    fireEvent.click(screen.getByTestId('community-tab-sources'));
    await waitFor(() => expect(screen.getByText(/Couldn't load/i)).toBeTruthy());
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
