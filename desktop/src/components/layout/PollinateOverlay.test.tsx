import type React from 'react';
/**
 * Tests for PollinateOverlay — the content-asset gallery.
 *
 * Covers: the works section (Pollinate v2) — thumbnails, Open in Canvas, the mark on
 * a failed version only, works outside the workspace, a works load error that leaves
 * the runs visible; fetch-once on open; the run gallery below it (newest-first cards,
 * newest expanded, client-side search); Gallery⇄Insights toggle (the run rollup strip
 * lives in Insights); Insights renders the publish-funnel with REAL numbers + NO token
 * panel; asset drawer opens with image + copy-caption + open-account and has no
 * mark-published controls any more.
 */
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';

const fetchAssets = vi.fn();
const fetchWorks = vi.fn();
const fetchAssetBody = vi.fn();
vi.mock('../../services/pollinate', async () => {
  const actual = await vi.importActual<typeof import('../../services/pollinate')>('../../services/pollinate');
  return {
    ...actual,
    pollinateService: {
      fetchAssets: () => fetchAssets(),
      fetchWorks: () => fetchWorks(),
      fetchAssetBody: (p: string) => fetchAssetBody(p),
    },
    assetThumbUrl: (p: string) => `http://x/api/workspace/file/raw?path=${p}`,
  };
});

import { PollinateContent } from './PollinateOverlay';
import { QueryClient as __QC, QueryClientProvider as __QCP } from '@tanstack/react-query';
import { render as __render } from '@testing-library/react';
// The overlay reads its data through TanStack Query (overlayQueries): a fresh client
// per render, so no cache carries over between tests.
const renderQ = (ui: React.ReactElement) =>
  __render(<__QCP client={new __QC({ defaultOptions: { queries: { retry: false } } })}>{ui}</__QCP>);

const ASSETS = {
  overall: {
    cardCount: 2, assetCount: 4, published: 1, ready: 3, inProgress: 1,
    platformDist: { xiaohongshu: 2, bilibili: 1 },
    formatDist: { poster: 3, narrative: 1 },
    domainDist: { ai_architecture: 1, swarm: 1 },
    knownChannels: ['bilibili', 'github', 'gongzhonghao', 'linkedin', 'twitter', 'xiaohongshu', 'youtube'],
  },
  cards: [
    {
      run: '2026-05-03-memory-is-the-moat', topic: 'AI 记忆 > AI 模型', domain: 'ai_architecture',
      status: 'completed', createdAt: '2026-05-03T18:30:00+08:00', hasRunJson: true,
      assetCount: 4, platforms: ['xiaohongshu', 'bilibili'], formats: ['poster', 'caption', 'narrative'],
      publishedCount: 1, readyCount: 2,
      assets: [
        { platform: 'xiaohongshu', format: 'poster', filePath: 'Knowledge/Pollinate/m/poster.png',
          fileName: 'poster.png', isImage: true, publishStatus: 'ready-to-publish' },
        // sibling caption for the xhs poster — the drawer should lazily fetch THIS body
        { platform: 'xiaohongshu', format: 'caption', filePath: 'Knowledge/Pollinate/m/caption.txt',
          fileName: 'caption.txt', isImage: false, publishStatus: 'ready-to-publish' },
        { platform: 'bilibili', format: 'poster', filePath: 'Knowledge/Pollinate/m/b.png',
          fileName: 'b.png', isImage: true, publishStatus: 'published' },
        { platform: 'gongzhonghao', format: 'narrative', filePath: 'Knowledge/Pollinate/m/n.md',
          fileName: 'narrative_full.md', isImage: false, publishStatus: 'ready' },
      ],
    },
    {
      run: '2026-04-26-agent-harness', topic: 'Agent Harness 对比', domain: 'ai-agents',
      status: 'running', createdAt: '2026-04-26T10:00:00+08:00', hasRunJson: true,
      assetCount: 1, platforms: ['xiaohongshu'], formats: ['poster'],
      publishedCount: 0, readyCount: 1,
      assets: [
        { platform: 'xiaohongshu', format: 'poster', filePath: 'Knowledge/Pollinate/a/p.png',
          fileName: 'p.png', isImage: true, publishStatus: 'ready' },
      ],
    },
    {
      // ALL-PUBLISHED card — the to-publish chip must EXCLUDE this one (Gate-2 MED: the
      // old fixture had no such card, so the filter test couldn't prove exclusion).
      run: '2026-03-01-all-published', topic: 'Fully Published Topic', domain: 'swarm',
      status: 'completed', createdAt: '2026-03-01T10:00:00+08:00', hasRunJson: true,
      assetCount: 1, platforms: ['bilibili'], formats: ['poster'],
      publishedCount: 1, readyCount: 0,
      assets: [
        { platform: 'bilibili', format: 'poster', filePath: 'Knowledge/Pollinate/ap/p.png',
          fileName: 'p.png', isImage: true, publishStatus: 'published' },
      ],
    },
  ],
};

const WORKS = {
  skipped: 0,
  works: [
    // newest cut v003 failed; current stays on the last passing version (design §13.2)
    { path: 'Projects/AIDLC/assets/ai-deck', inWorkspace: true, kind: 'deck', title: 'AI Native deck',
      current: 'v002', latest: 'v003', status: 'failed', updated: '2026-11-25T08:00:00Z',
      thumbnail: 'Projects/AIDLC/assets/ai-deck/out/slides/01-s-7f3a.png',
      mainOutput: 'Projects/AIDLC/assets/ai-deck/out/deck.html' },
    { path: 'Knowledge/Pollinate/2026-11-24-launch', inWorkspace: true, kind: 'poster', title: 'Launch poster',
      current: 'v001', latest: 'v001', status: 'ok', updated: '2026-11-24T09:00:00Z',
      thumbnail: 'Knowledge/Pollinate/2026-11-24-launch/out/poster.png',
      mainOutput: 'Knowledge/Pollinate/2026-11-24-launch/out/poster.png' },
    { path: '/home/me/Desktop/AI-Native/deck', inWorkspace: false, kind: 'deck', title: 'Outside deck',
      current: 'v002', latest: 'v002', status: 'ok', updated: '2026-11-20T09:00:00Z',
      thumbnail: '/home/me/Desktop/AI-Native/deck/out/slides/01-s-1111.png',
      mainOutput: '/home/me/Desktop/AI-Native/deck/out/deck.html' },
  ],
};

afterEach(() => { cleanup(); vi.clearAllMocks(); });
beforeEach(() => {
  fetchAssets.mockResolvedValue(ASSETS);
  fetchWorks.mockResolvedValue(WORKS);
  fetchAssetBody.mockResolvedValue('小红书文案正文 — 复制我去发布 #AI');
});

function renderAndOpen(onDispatch = vi.fn().mockReturnValue(true), close = vi.fn()) {
  // M4: PollinateContent renders immediately (host owns open + fresh mount per open).
  renderQ(<PollinateContent onDispatch={onDispatch} close={close} />);
  return { onDispatch, close };
}

describe('PollinateOverlay', () => {
  it('fetches once on open and renders works first, then the newest-first run cards', async () => {
    renderAndOpen();
    await waitFor(() => expect(screen.getByTestId('pollinate-card-2026-05-03-memory-is-the-moat')).toBeInTheDocument());
    expect(screen.getByTestId('pollinate-card-2026-04-26-agent-harness')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId('pollinate-work-Projects/AIDLC/assets/ai-deck')).toBeInTheDocument());
    // works come before the earlier runs in the gallery
    const gallery = screen.getByTestId('pollinate-gallery');
    const works = screen.getByTestId('pollinate-works');
    const runCard = screen.getByTestId('pollinate-card-2026-05-03-memory-is-the-moat');
    expect(gallery.contains(works)).toBe(true);
    expect(works.compareDocumentPosition(runCard) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // the run rollup strip is an Insights detail, not the gallery's headline
    expect(screen.queryByTestId('pollinate-overall')).toBeNull();
    expect(fetchAssets).toHaveBeenCalledTimes(1); // no polling
    expect(fetchWorks).toHaveBeenCalledTimes(1);
  });

  it('expands the newest card by default (asset grid visible)', async () => {
    renderAndOpen();
    await waitFor(() => expect(screen.getByTestId('pollinate-card-2026-05-03-memory-is-the-moat')).toBeInTheDocument());
    // newest card's first asset cell is rendered (expanded)
    expect(screen.getByTestId('pollinate-asset-2026-05-03-memory-is-the-moat-0')).toBeInTheDocument();
  });

  it('client-side search filters the card list', async () => {
    renderAndOpen();
    await waitFor(() => expect(screen.getByTestId('pollinate-search')).toBeInTheDocument());
    fireEvent.change(screen.getByTestId('pollinate-search'), { target: { value: 'harness' } });
    await waitFor(() => {
      expect(screen.queryByTestId('pollinate-card-2026-05-03-memory-is-the-moat')).toBeNull();
      expect(screen.getByTestId('pollinate-card-2026-04-26-agent-harness')).toBeInTheDocument();
    });
  });

  it('toggles to Insights and shows the publish funnel with real numbers, NO token panel', async () => {
    renderAndOpen();
    await waitFor(() => expect(screen.getByTestId('pollinate-view-insights')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('pollinate-view-insights'));
    await waitFor(() => expect(screen.getByTestId('pollinate-insights')).toBeInTheDocument());
    expect(screen.getByTestId('pollinate-overall').textContent).toContain('4'); // assets
    const txt = screen.getByTestId('pollinate-insights').textContent || '';
    expect(txt).toContain('Publish funnel');
    expect(txt).toContain('By channel');
    // real numbers: 4 produced, 1 published
    expect(txt).toContain('Published');
    // token panel must be ABSENT (pollinate can't attribute per-run tokens)
    expect(txt.toLowerCase()).not.toContain('token');
  });

  it('opens the asset drawer with image + copy + open-account (manual publish)', async () => {
    renderAndOpen();
    await waitFor(() => expect(screen.getByTestId('pollinate-asset-2026-05-03-memory-is-the-moat-0')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('pollinate-asset-2026-05-03-memory-is-the-moat-0'));
    await waitFor(() => expect(screen.getByTestId('pollinate-asset-drawer')).toBeInTheDocument());
    expect(screen.getByTestId('pollinate-copy-btn')).toBeInTheDocument();
    expect(screen.getByTestId('pollinate-open-account-btn')).toBeInTheDocument();
  });

  // ── Gap 1: caption body lazily fetched + rendered + copied (design moment ②) ──
  it('lazily fetches + renders the sibling caption body for an image asset', async () => {
    renderAndOpen();
    await waitFor(() => expect(screen.getByTestId('pollinate-asset-2026-05-03-memory-is-the-moat-0')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('pollinate-asset-2026-05-03-memory-is-the-moat-0')); // the xhs poster
    await waitFor(() => expect(screen.getByTestId('pollinate-caption-body')).toBeInTheDocument());
    // fetched the SIBLING caption.txt (same platform), NOT the poster png
    expect(fetchAssetBody).toHaveBeenCalledWith('Knowledge/Pollinate/m/caption.txt');
    expect(screen.getByTestId('pollinate-caption-body').textContent).toContain('复制我去发布');
  });

  it('copies the caption BODY text, not the path', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    renderAndOpen();
    await waitFor(() => expect(screen.getByTestId('pollinate-asset-2026-05-03-memory-is-the-moat-0')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('pollinate-asset-2026-05-03-memory-is-the-moat-0'));
    await waitFor(() => expect(screen.getByTestId('pollinate-caption-body')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('pollinate-copy-btn'));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('小红书文案正文 — 复制我去发布 #AI'));
  });

  it('prefers a real caption sibling over a publish-kit (Gate-2 HIGH)', async () => {
    // card whose xhs poster has BOTH a caption.txt and a publish-kit.md sibling
    fetchAssets.mockResolvedValue({
      overall: { ...ASSETS.overall },
      cards: [{
        run: 'r-kit', topic: 'Kit topic', domain: 'swarm', status: 'completed',
        createdAt: '2026-05-01T00:00:00+08:00', hasRunJson: true, assetCount: 3,
        platforms: ['xiaohongshu'], formats: ['poster', 'caption'], publishedCount: 0, readyCount: 3,
        assets: [
          { platform: 'xiaohongshu', format: 'poster', filePath: 'K/r-kit/poster.png',
            fileName: 'poster.png', isImage: true, publishStatus: 'ready' },
          { platform: 'xiaohongshu', format: 'caption', filePath: 'K/r-kit/publish-kit.md',
            fileName: 'publish-kit.md', isImage: false, publishStatus: 'ready' },
          { platform: 'xiaohongshu', format: 'caption', filePath: 'K/r-kit/caption.txt',
            fileName: 'caption.txt', isImage: false, publishStatus: 'ready' },
        ],
      }],
    });
    renderAndOpen();
    await waitFor(() => expect(screen.getByTestId('pollinate-asset-r-kit-0')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('pollinate-asset-r-kit-0')); // the poster
    await waitFor(() => expect(screen.getByTestId('pollinate-caption-body')).toBeInTheDocument());
    // resolved the clean caption.txt, NOT publish-kit.md
    expect(fetchAssetBody).toHaveBeenCalledWith('K/r-kit/caption.txt');
    expect(fetchAssetBody).not.toHaveBeenCalledWith('K/r-kit/publish-kit.md');
  });

  // ── Gap 2: missing-platform produce buttons in the drawer ──
  it('offers Produce buttons for platforms not yet produced on this topic', async () => {
    renderAndOpen();
    await waitFor(() => expect(screen.getByTestId('pollinate-asset-2026-05-03-memory-is-the-moat-0')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('pollinate-asset-2026-05-03-memory-is-the-moat-0'));
    await waitFor(() => expect(screen.getByTestId('pollinate-missing-platforms')).toBeInTheDocument());
    // card has xhs+bili → youtube/github/etc should be offered, xhs should NOT
    expect(screen.getByTestId('pollinate-produce-platform-youtube')).toBeInTheDocument();
    expect(screen.queryByTestId('pollinate-produce-platform-xiaohongshu')).toBeNull();
  });

  // ── Gap 3: domain chips + to-publish state filter ──
  it('domain chip filters the card list', async () => {
    renderAndOpen();
    await waitFor(() => expect(screen.getByTestId('pollinate-domain-chip-ai-agents')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('pollinate-domain-chip-ai-agents'));
    await waitFor(() => {
      expect(screen.getByTestId('pollinate-card-2026-04-26-agent-harness')).toBeInTheDocument();
      expect(screen.queryByTestId('pollinate-card-2026-05-03-memory-is-the-moat')).toBeNull();
    });
  });

  it('to-publish chip EXCLUDES a fully-published card', async () => {
    renderAndOpen();
    await waitFor(() => expect(screen.getByTestId('pollinate-card-2026-03-01-all-published')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('pollinate-chip-to-publish'));
    await waitFor(() => {
      // the all-published card disappears; a card with unpublished assets stays
      expect(screen.queryByTestId('pollinate-card-2026-03-01-all-published')).toBeNull();
      expect(screen.getByTestId('pollinate-card-2026-05-03-memory-is-the-moat')).toBeInTheDocument();
    });
  });

  // ── Gap 4: neglected channel (0 assets) visible in Insights ──
  it('Insights by-channel surfaces a fully-neglected channel (0 assets)', async () => {
    renderAndOpen();
    await waitFor(() => expect(screen.getByTestId('pollinate-view-insights')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('pollinate-view-insights'));
    await waitFor(() => expect(screen.getByTestId('pollinate-insights')).toBeInTheDocument());
    // youtube has 0 assets in the fixture but IS a known channel → must appear
    expect(screen.getByTestId('pollinate-insights').textContent).toContain('youtube');
  });

  // ── Pollinate v2: the works section ──
  it('works: each card shows its cover thumbnail, title and version', async () => {
    renderAndOpen();
    const card = await screen.findByTestId('pollinate-work-Projects/AIDLC/assets/ai-deck');
    const img = card.querySelector('img');
    expect(img?.getAttribute('src')).toBe(
      'http://x/api/workspace/file/raw?path=Projects/AIDLC/assets/ai-deck/out/slides/01-s-7f3a.png');
    expect(card.textContent).toContain('AI Native deck');
    expect(card.textContent).toContain('v002');
    expect(card.textContent).toContain('2026-11-25');
  });

  it('works: Open in Canvas closes the overlay and opens the main output', async () => {
    const { close } = renderAndOpen();
    const opened: string[] = [];
    const onOpen = (e: Event) => opened.push((e as CustomEvent<{ path: string }>).detail.path);
    document.addEventListener('swarm:open-file', onOpen);
    try {
      fireEvent.click(await screen.findByTestId('pollinate-work-open-Projects/AIDLC/assets/ai-deck'));
      expect(close).toHaveBeenCalledTimes(1);
      expect(opened).toEqual(['Projects/AIDLC/assets/ai-deck/out/deck.html']);
    } finally {
      document.removeEventListener('swarm:open-file', onOpen);
    }
  });

  it('works: only a card whose newest version failed is marked', async () => {
    renderAndOpen();
    const failed = await screen.findByTestId('pollinate-work-Projects/AIDLC/assets/ai-deck');
    const ok = screen.getByTestId('pollinate-work-Knowledge/Pollinate/2026-11-24-launch');
    expect(failed.querySelector('[data-testid="pollinate-work-failed"]')).not.toBeNull();
    expect(ok.querySelector('[data-testid="pollinate-work-failed"]')).toBeNull();
    expect(screen.getAllByTestId('pollinate-work-failed')).toHaveLength(1);
    const tip = screen.getByTestId('pollinate-work-failed').getAttribute('title') ?? '';
    expect(tip).toContain('v003');   // the version that failed
    expect(tip).toContain('v002');   // the one the card shows
  });

  it('works: a work outside the workspace has no Open in Canvas and no thumbnail', async () => {
    renderAndOpen();
    const card = await screen.findByTestId('pollinate-work-/home/me/Desktop/AI-Native/deck');
    expect(screen.queryByTestId('pollinate-work-open-/home/me/Desktop/AI-Native/deck')).toBeNull();
    expect(card.querySelector('img')).toBeNull();
    expect(card.textContent).toContain('Outside the workspace');
  });

  it('no works and no runs: one empty state, no Earlier runs toolbar', async () => {
    fetchWorks.mockResolvedValue({ works: [], skipped: 0 });
    fetchAssets.mockResolvedValue({ overall: { ...ASSETS.overall, cardCount: 0 }, cards: [] });
    renderAndOpen();
    expect(await screen.findByTestId('pollinate-works-empty')).toBeInTheDocument();
    await waitFor(() => expect(fetchAssets).toHaveBeenCalled());
    expect(screen.queryByTestId('pollinate-earlier-runs')).toBeNull();
    expect(screen.queryByTestId('pollinate-search')).toBeNull();
  });

  it('works: an empty registry says so; skipped entries are counted', async () => {
    fetchWorks.mockResolvedValue({ works: [], skipped: 2 });
    renderAndOpen();
    expect(await screen.findByTestId('pollinate-works-empty')).toBeInTheDocument();
    expect(screen.getByTestId('pollinate-works-skipped').textContent).toContain('2 registered works');
  });

  it('works: a failed works fetch shows its own error while the runs still render', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    fetchWorks.mockRejectedValue(new Error('down'));
    renderAndOpen();
    expect(await screen.findByTestId('pollinate-works-error')).toBeInTheDocument();
    expect(screen.getByTestId('pollinate-card-2026-05-03-memory-is-the-moat')).toBeInTheDocument();
    expect(screen.queryByTestId('pollinate-load-error')).toBeNull();
  });

  it('the asset drawer has no mark-published controls any more', async () => {
    renderAndOpen();
    fireEvent.click(await screen.findByTestId('pollinate-asset-2026-05-03-memory-is-the-moat-0'));
    await waitFor(() => expect(screen.getByTestId('pollinate-asset-drawer')).toBeInTheDocument());
    expect(screen.queryByTestId('pollinate-mark-published-btn')).toBeNull();
    expect(screen.queryByTestId('pollinate-posted-url-input')).toBeNull();
    expect(screen.queryByTestId('pollinate-unpublish-btn')).toBeNull();
  });
});
