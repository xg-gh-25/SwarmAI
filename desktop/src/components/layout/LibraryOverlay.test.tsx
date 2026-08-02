/**
 * Tests for LibraryOverlay — the agent's bookshelf (Native store + Mounts).
 *
 * Focus of THIS suite: the loading / error / empty three-state discipline of the
 * Browse (native) + Recent tabs. Before the fix, native/recent useQuery discarded
 * isLoading/isError, so BrowseTab rendered `cats.length===0 → "Loading categories…"`
 * — meaning a FAILED or genuinely-empty fetch spun "Loading…" forever with no error
 * signal + no recovery. These tests pin the three distinct states:
 *   pending → Loading · error → message + working Retry(refetch) · empty → No categories.
 *
 * The api client is mocked at the boundary (services/api); the component is
 * backend-primary and invents no data (R30).
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, act, cleanup, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../../services/api', () => ({
  default: { get: vi.fn(), post: vi.fn() },
}));
import api from '../../services/api';
import { LibraryOverlay } from './LibraryOverlay';
import { __resetActiveOverlayEvent } from './useExclusiveOverlay';

const NATIVE_OK = {
  source: 'native', root: 'Knowledge/', category_count: 2,
  categories: [
    { name: 'Designs', file_count: 12, total_bytes: 34000 },
    { name: 'Notes', file_count: 5, total_bytes: 8000 },
  ],
};
const NATIVE_EMPTY = { source: 'native', root: 'Knowledge/', category_count: 0, categories: [] };
const RECENT_OK = {
  window_days: 7, count: 1,
  items: [{ path: 'Knowledge/Notes/x.md', category: 'Notes', mtime: 1785600000, size: 100, source: 'session' }],
};
const RECENT_EMPTY = { window_days: 7, count: 0, items: [] };
const MOUNTS_OK = { count: 0, mounts: [], registry_ready: true };

type Handler = (url: string) => Promise<{ data: unknown }>;
function mockApi(h: Handler) {
  (api.get as ReturnType<typeof vi.fn>).mockImplementation(h);
}
// default: everything resolves OK
function mockAllOk() {
  mockApi((url: string) => {
    if (url.includes('/native')) return Promise.resolve({ data: NATIVE_OK });
    if (url.includes('/recent')) return Promise.resolve({ data: RECENT_OK });
    if (url.includes('/mounts')) return Promise.resolve({ data: MOUNTS_OK });
    return Promise.resolve({ data: {} });
  });
}

function renderOverlay() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <LibraryOverlay />
    </QueryClientProvider>,
  );
}
function openOverlay() {
  act(() => { window.dispatchEvent(new CustomEvent('swarm:show-library')); });
}

beforeEach(() => {
  __resetActiveOverlayEvent();
  mockAllOk();
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('LibraryOverlay — Browse native three-state', () => {
  it('renders categories when the native fetch succeeds (not "Loading…")', async () => {
    renderOverlay();
    openOverlay();
    await screen.findByTestId('library-overlay');
    expect(await screen.findByTestId('library-cat-Designs')).toBeInTheDocument();
    expect(screen.getByTestId('library-cat-Notes')).toBeInTheDocument();
    // the old permanent-loading text must NOT be showing once data arrived
    expect(screen.queryByText(/Loading categories/i)).toBeNull();
  });

  it('shows an ERROR state with a working Retry when the native fetch fails (no infinite Loading)', async () => {
    mockApi((url: string) => {
      if (url.includes('/native')) return Promise.reject(new Error('503 workspace not init'));
      if (url.includes('/recent')) return Promise.resolve({ data: RECENT_OK });
      return Promise.resolve({ data: MOUNTS_OK });
    });
    renderOverlay();
    openOverlay();
    await screen.findByTestId('library-overlay');
    // error surface appears — NOT the "Loading categories…" spinner
    const err = await screen.findByTestId('library-native-error');
    expect(err).toBeInTheDocument();
    expect(screen.queryByText(/Loading categories/i)).toBeNull();

    // Retry re-issues the native fetch (recovers transient 503 without reopening)
    const before = (api.get as ReturnType<typeof vi.fn>).mock.calls
      .filter((c) => String(c[0]).includes('/native')).length;
    const retry = screen.getByTestId('library-native-retry');
    act(() => { retry.click(); });
    await waitFor(() => {
      const after = (api.get as ReturnType<typeof vi.fn>).mock.calls
        .filter((c) => String(c[0]).includes('/native')).length;
      expect(after).toBeGreaterThan(before);
    });
  });

  it('shows the pending "Loading categories…" state mid-flight (before native resolves)', async () => {
    // Never-resolving native fetch → the query stays pending → the Loading branch renders.
    let release: (v: { data: unknown }) => void = () => {};
    mockApi((url: string) => {
      if (url.includes('/native')) return new Promise((res) => { release = res; });
      if (url.includes('/recent')) return Promise.resolve({ data: RECENT_OK });
      return Promise.resolve({ data: MOUNTS_OK });
    });
    renderOverlay();
    openOverlay();
    await screen.findByTestId('library-overlay');
    expect(await screen.findByText(/Loading categories/i)).toBeInTheDocument();
    // neither settled surface should be present while pending
    expect(screen.queryByTestId('library-native-error')).toBeNull();
    expect(screen.queryByTestId('library-native-empty')).toBeNull();
    // resolve so the test doesn't leak a dangling promise
    act(() => { release({ data: NATIVE_OK }); });
  });

  it('shows an EMPTY state (not "Loading…") when native succeeds with zero categories', async () => {
    mockApi((url: string) => {
      if (url.includes('/native')) return Promise.resolve({ data: NATIVE_EMPTY });
      if (url.includes('/recent')) return Promise.resolve({ data: RECENT_EMPTY });
      return Promise.resolve({ data: MOUNTS_OK });
    });
    renderOverlay();
    openOverlay();
    await screen.findByTestId('library-overlay');
    expect(await screen.findByTestId('library-native-empty')).toBeInTheDocument();
    // empty is settled, not pending — the old spinner text must be gone
    expect(screen.queryByText(/Loading categories/i)).toBeNull();
  });
});

describe('LibraryOverlay — Recent three-state', () => {
  async function openRecent() {
    await screen.findByTestId('library-overlay');
    act(() => { screen.getByTestId('library-tab-recent').click(); });
    return screen.findByTestId('library-panel-recent');
  }

  it('shows an ERROR state with Retry when the recent fetch fails', async () => {
    mockApi((url: string) => {
      if (url.includes('/native')) return Promise.resolve({ data: NATIVE_OK });
      if (url.includes('/recent')) return Promise.reject(new Error('boom'));
      return Promise.resolve({ data: MOUNTS_OK });
    });
    renderOverlay();
    openOverlay();
    await openRecent();
    expect(await screen.findByTestId('library-recent-error')).toBeInTheDocument();
    // Retry re-issues the recent fetch (symmetric with native's recovery path)
    const before = (api.get as ReturnType<typeof vi.fn>).mock.calls
      .filter((c) => String(c[0]).includes('/recent')).length;
    act(() => { screen.getByTestId('library-recent-retry').click(); });
    await waitFor(() => {
      const after = (api.get as ReturnType<typeof vi.fn>).mock.calls
        .filter((c) => String(c[0]).includes('/recent')).length;
      expect(after).toBeGreaterThan(before);
    });
  });

  it('shows the empty-week message when recent succeeds with zero items', async () => {
    mockApi((url: string) => {
      if (url.includes('/native')) return Promise.resolve({ data: NATIVE_OK });
      if (url.includes('/recent')) return Promise.resolve({ data: RECENT_EMPTY });
      return Promise.resolve({ data: MOUNTS_OK });
    });
    renderOverlay();
    openOverlay();
    const panel = await openRecent();
    await waitFor(() => expect(panel.textContent).toMatch(/Nothing new in the last week/i));
    expect(screen.queryByTestId('library-recent-error')).toBeNull();
  });
});
