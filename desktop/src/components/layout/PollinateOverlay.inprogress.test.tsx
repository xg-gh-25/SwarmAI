import type React from 'react';
/**
 * PollinateOverlay: every work in progress is a card in the top zone (the backend's
 * in_progress: an open revision, no passed version yet, a video with no film), every other
 * work a row; a video in progress shows its stages and scene frame count and opens the chat
 * that registered it; a document with no version shows that none is cut yet; while the
 * works load the overlay says so.
 */
import { describe, it, expect, afterEach, vi } from 'vitest';
import { screen, cleanup, fireEvent, waitFor, within } from '@testing-library/react';

const fetchWorks = vi.fn();
vi.mock('../../services/pollinate', async () => {
  const actual = await vi.importActual<typeof import('../../services/pollinate')>('../../services/pollinate');
  return {
    ...actual,
    pollinateService: { fetchWorks: () => fetchWorks(), fetchWorkView: vi.fn(), fetchEval: vi.fn().mockResolvedValue(null) },
    assetThumbUrl: (p: string) => `http://x/raw?path=${p}`,
  };
});

import { PollinateContent } from './PollinateOverlay';
import { workToCamel } from '../../services/pollinate';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render as _r } from '@testing-library/react';

const renderQ = (ui: React.ReactElement) =>
  _r(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{ui}</QueryClientProvider>);

const MAKING = workToCamel({
  path: 'Services/x/beyond', in_workspace: true, kind: 'video', title: 'Beyond AI', brain: 'General',
  versions: [], in_progress: true, progress: { stage: 'scenes', scenes: 22, framed: 14 }, session: 'S-video',
  updated: '2026-11-26T03:20:00Z',
});
const FRESH = workToCamel({
  path: 'Knowledge/Pollinate/fresh', in_workspace: true, kind: 'deck', title: 'Fresh deck', brain: 'General',
  current: null, versions: [], in_progress: true, updated: '2026-11-26T03:00:00Z',
});
const DONE = workToCamel({
  path: 'Services/x/edge', in_workspace: true, kind: 'video', title: 'Edge AI', brain: 'General',
  versions: [], in_progress: false, progress: { stage: 'film', scenes: 9, framed: 9 }, main_output: 'Services/x/edge/out/e.mp4',
});

describe('PollinateOverlay in progress', () => {
  afterEach(() => { cleanup(); vi.clearAllMocks(); });

  it('puts exactly the in-progress works in the top zone', async () => {
    fetchWorks.mockResolvedValue({ works: [MAKING, FRESH, DONE], skipped: 0 });
    renderQ(<PollinateContent onDispatch={() => true} close={() => {}} />);
    const zone = await screen.findByTestId('pollinate-revising');
    expect(within(zone).getByTestId(`pollinate-card-${MAKING.path}`)).toBeTruthy();
    expect(within(zone).getByTestId(`pollinate-card-${FRESH.path}`)).toBeTruthy();
    expect(within(zone).queryByTestId(`pollinate-card-${DONE.path}`)).toBeNull();
    expect(screen.getByTestId(`pollinate-row-${DONE.path}`)).toBeTruthy();
    expect(screen.queryByTestId(`pollinate-row-${MAKING.path}`)).toBeNull();
    expect(within(zone).getByTestId('pollinate-revising-head').textContent).toContain('2');
  });

  it('a video in progress shows its stages, its frame count and opens its chat', async () => {
    fetchWorks.mockResolvedValue({ works: [MAKING], skipped: 0 });
    const onContinue = vi.fn().mockResolvedValue(true);
    renderQ(<PollinateContent onDispatch={() => true} close={() => {}} onContinue={onContinue} />);
    const card = await screen.findByTestId(`pollinate-card-${MAKING.path}`);
    const stages = within(card).getByTestId('pollinate-stages');
    const states = Array.from(stages.querySelectorAll('[data-stage]')).map((n) => [n.getAttribute('data-stage'), n.getAttribute('data-state')]);
    expect(states).toEqual([['brief', 'done'], ['storyboard', 'done'], ['scenes', 'at'], ['film', 'todo']]);
    expect(stages.textContent).toContain('14 / 22');
    expect(within(card).queryByTestId(`pollinate-track-${MAKING.path}`)).toBeNull();
    expect(within(card).queryByTestId('pollinate-card-canvas')).toBeNull();
    fireEvent.click(within(card).getByTestId('pollinate-card-session'));
    await waitFor(() => expect(onContinue).toHaveBeenCalledWith('S-video', ''));
  });

  it('a document with no version says none is cut yet', async () => {
    fetchWorks.mockResolvedValue({ works: [FRESH], skipped: 0 });
    renderQ(<PollinateContent onDispatch={() => true} close={() => {}} />);
    const card = await screen.findByTestId(`pollinate-card-${FRESH.path}`);
    expect(within(card).getByTestId('pollinate-card-state').textContent).toMatch(/No version cut yet|还没有出过版本/);
    expect(within(card).queryByTestId('pollinate-card-scope')).toBeNull();
  });

  it('says it is loading until the works arrive', async () => {
    let resolve: (v: unknown) => void = () => {};
    fetchWorks.mockReturnValue(new Promise((r) => { resolve = r; }));
    renderQ(<PollinateContent onDispatch={() => true} close={() => {}} />);
    expect(screen.getByTestId('pollinate-loading')).toBeTruthy();
    resolve({ works: [DONE], skipped: 0 });
    await screen.findByTestId(`pollinate-row-${DONE.path}`);
    expect(screen.queryByTestId('pollinate-loading')).toBeNull();
  });
});
