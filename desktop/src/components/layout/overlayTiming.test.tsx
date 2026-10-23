/**
 * Tests for overlayTiming — the dev-build timing table XG reads after clicking
 * through the overlays (docs/design/left-nav-overlay-finish.md §4).
 *
 * Drives the real OverlayHost with a surface whose content reads a real overlay
 * query (Pollinate's, service mocked). Verifies: with timing enabled, an open
 * records `window.__overlayTimings[id]` with the code time (open → content
 * mounted) and the data time (open → the overlay's NAV_PREFETCH queries all have
 * data), `cache: 'miss'` on a cold open and `'hit'` on a reopen; with timing off
 * (tests and production) nothing is recorded.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, act, cleanup } from '@testing-library/react';
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query';

let resolveAssets: (v: unknown) => void = () => {};
vi.mock('../../services/pollinate', () => ({
  pollinateService: { fetchAssets: () => new Promise((r) => { resolveAssets = r; }) },
}));

import { OverlayProvider, useOverlay } from '../../contexts/OverlayContext';
import { OverlayHost } from './OverlayHost';
import { registerOverlay } from './overlayRegistry';
import { overlayQuery } from './overlayQueries';
import { overlayTiming, type OverlayTimingRow } from './overlayTiming';

function Content() {
  const q = useQuery(overlayQuery.pollinate());
  return <div data-testid="timing-content">{q.data ? 'data' : 'waiting'}</div>;
}
registerOverlay({ id: 'pollinate', render: () => <Content /> });

function Harness() {
  const { openOverlay, closeOverlay } = useOverlay();
  return (
    <div>
      <button data-testid="open" onClick={() => openOverlay('pollinate')}>open</button>
      <button data-testid="close" onClick={() => closeOverlay()}>close</button>
      <OverlayHost />
    </div>
  );
}

const table = () => (window as unknown as { __overlayTimings?: Record<string, OverlayTimingRow> }).__overlayTimings;

describe('overlayTiming — window.__overlayTimings', () => {
  const wasEnabled = overlayTiming.enabled;
  beforeEach(() => { delete (window as unknown as { __overlayTimings?: unknown }).__overlayTimings; });
  afterEach(() => { overlayTiming.enabled = wasEnabled; cleanup(); });

  it('records code and data times; a cold open is a miss, a reopen a hit', async () => {
    overlayTiming.enabled = true;
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={qc}><OverlayProvider><Harness /></OverlayProvider></QueryClientProvider>);

    act(() => screen.getByTestId('open').click());
    await screen.findByTestId('timing-content');
    let row = table()!.pollinate;
    expect(row.cache).toBe('miss');
    expect(typeof row.code_ms).toBe('number');
    expect(row.data_ms).toBeNull();                 // the fetch has not returned

    await act(async () => { resolveAssets([]); });
    expect(await screen.findByText('data')).toBeInTheDocument();
    row = table()!.pollinate;
    expect(typeof row.data_ms).toBe('number');

    act(() => screen.getByTestId('close').click());
    act(() => screen.getByTestId('open').click());
    await screen.findByText('data');
    row = table()!.pollinate;
    expect(row.cache).toBe('hit');
    expect(row.data_ms).toBe(0);
  });

  it('records nothing when timing is off (tests, production)', async () => {
    overlayTiming.enabled = false;
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={qc}><OverlayProvider><Harness /></OverlayProvider></QueryClientProvider>);
    act(() => screen.getByTestId('open').click());
    await screen.findByTestId('timing-content');
    expect(table()).toBeUndefined();
  });
});
