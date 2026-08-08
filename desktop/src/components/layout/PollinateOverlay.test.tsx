/**
 * Tests for PollinateOverlay — the content-asset gallery.
 *
 * Covers: fetch-once on open; Gallery renders newest-first cards + asset grid;
 * newest card expanded by default; client-side search filters cards; Gallery⇄Insights
 * toggle; Insights renders the publish-funnel with REAL numbers + NO token panel;
 * asset drawer opens with image + copy-caption + open-account (manual publish).
 */
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, screen, cleanup, act, fireEvent, waitFor } from '@testing-library/react';

const fetchAssets = vi.fn();
const fetchTopicDetail = vi.fn();
const fetchAssetBody = vi.fn();
const markPublished = vi.fn();
vi.mock('../../services/pollinate', async () => {
  const actual = await vi.importActual<typeof import('../../services/pollinate')>('../../services/pollinate');
  return {
    ...actual,
    pollinateService: {
      fetchAssets: () => fetchAssets(),
      fetchTopicDetail: (r: string) => fetchTopicDetail(r),
      fetchAssetBody: (p: string) => fetchAssetBody(p),
      markPublished: (...a: unknown[]) => markPublished(...a),
    },
    assetThumbUrl: (p: string) => `http://x/api/workspace/file/raw?path=${p}`,
  };
});

import { PollinateContent } from './PollinateOverlay';

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
          fileName: 'poster.png', isImage: true, publishStatus: 'ready-to-publish',
          assetId: 'a'.repeat(40), postedUrl: null },
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

afterEach(() => { cleanup(); vi.clearAllMocks(); });
beforeEach(() => {
  fetchAssets.mockResolvedValue(ASSETS);
  fetchTopicDetail.mockResolvedValue(null);
  fetchAssetBody.mockResolvedValue('小红书文案正文 — 复制我去发布 #AI');
  markPublished.mockResolvedValue({ publishStatus: 'published', postedUrl: 'https://xhs.com/p/1' });
});

function renderAndOpen(onDispatch = vi.fn().mockReturnValue(true)) {
  // M4: PollinateContent renders immediately (host owns open + fresh mount per open).
  render(<PollinateContent onDispatch={onDispatch} close={() => {}} />);
  return { onDispatch };
}

describe('PollinateOverlay', () => {
  it('fetches once on open and renders overall + newest-first cards', async () => {
    renderAndOpen();
    await waitFor(() => expect(screen.getByTestId('pollinate-overall')).toBeInTheDocument());
    expect(screen.getByTestId('pollinate-overall').textContent).toContain('4'); // assets
    // both content cards present
    expect(screen.getByTestId('pollinate-card-2026-05-03-memory-is-the-moat')).toBeInTheDocument();
    expect(screen.getByTestId('pollinate-card-2026-04-26-agent-harness')).toBeInTheDocument();
    expect(fetchAssets).toHaveBeenCalledTimes(1); // no polling
  });

  it('expands the newest card by default (asset grid visible)', async () => {
    renderAndOpen();
    await waitFor(() => expect(screen.getByTestId('pollinate-overall')).toBeInTheDocument());
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

  // ── P1: Mark-published write path (run_b290eb6f) ──
  it('Mark published calls the service with the asset id + posted URL, then reflects it in the OPEN drawer', async () => {
    renderAndOpen();
    await waitFor(() => expect(screen.getByTestId('pollinate-asset-2026-05-03-memory-is-the-moat-0')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('pollinate-asset-2026-05-03-memory-is-the-moat-0')); // the xhs poster (ready-to-publish)
    await waitFor(() => expect(screen.getByTestId('pollinate-mark-published-btn')).toBeInTheDocument());
    // enter a posted URL, then mark published
    fireEvent.change(screen.getByTestId('pollinate-posted-url-input'), { target: { value: 'https://xhs.com/p/1' } });
    await act(async () => { fireEvent.click(screen.getByTestId('pollinate-mark-published-btn')); });
    // service called with (run, assetId, true, url)
    expect(markPublished).toHaveBeenCalledWith('2026-05-03-memory-is-the-moat', 'a'.repeat(40), true, 'https://xhs.com/p/1');
    // GUI101 write→read: the OPEN drawer must now show published (optimistic patch of `selected`),
    // NOT stay stale on 'ready-to-publish' — this is the exact gap Gate-1 flagged.
    await waitFor(() => expect(screen.getByTestId('pollinate-unpublish-btn')).toBeInTheDocument());
    expect(screen.getByTestId('pollinate-posted-url')).toHaveAttribute('href', 'https://xhs.com/p/1');
    // gallery re-fetch fired too (rollup counts refresh)
    expect(fetchAssets).toHaveBeenCalledTimes(2);
  });

  it('a failed markPublished leaves the drawer on its prior state + shows retry', async () => {
    markPublished.mockResolvedValueOnce(null); // service failure
    renderAndOpen();
    await waitFor(() => expect(screen.getByTestId('pollinate-asset-2026-05-03-memory-is-the-moat-0')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('pollinate-asset-2026-05-03-memory-is-the-moat-0'));
    await waitFor(() => expect(screen.getByTestId('pollinate-mark-published-btn')).toBeInTheDocument());
    await act(async () => { fireEvent.click(screen.getByTestId('pollinate-mark-published-btn')); });
    // still shows the mark-published button (not flipped to published) + a failure hint
    await waitFor(() => expect(screen.getByTestId('pollinate-mark-published-btn').textContent).toContain('Failed'));
    expect(screen.queryByTestId('pollinate-unpublish-btn')).toBeNull();
  });
});
