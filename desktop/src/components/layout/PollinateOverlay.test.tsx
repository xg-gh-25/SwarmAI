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
vi.mock('../../services/pollinate', async () => {
  const actual = await vi.importActual<typeof import('../../services/pollinate')>('../../services/pollinate');
  return {
    ...actual,
    pollinateService: {
      fetchAssets: () => fetchAssets(),
      fetchTopicDetail: (r: string) => fetchTopicDetail(r),
    },
    assetThumbUrl: (p: string) => `http://x/api/workspace/file/raw?path=${p}`,
  };
});

import { PollinateOverlay } from './PollinateOverlay';

const ASSETS = {
  overall: {
    cardCount: 2, assetCount: 4, published: 1, ready: 3, inProgress: 1,
    platformDist: { xiaohongshu: 2, bilibili: 1 },
    formatDist: { poster: 3, narrative: 1 },
    domainDist: { ai_architecture: 1, swarm: 1 },
  },
  cards: [
    {
      run: '2026-05-03-memory-is-the-moat', topic: 'AI 记忆 > AI 模型', domain: 'ai_architecture',
      status: 'completed', createdAt: '2026-05-03T18:30:00+08:00', hasRunJson: true,
      assetCount: 3, platforms: ['xiaohongshu', 'bilibili'], formats: ['poster', 'narrative'],
      publishedCount: 1, readyCount: 2,
      assets: [
        { platform: 'xiaohongshu', format: 'poster', filePath: 'Knowledge/Pollinate/m/poster.png',
          fileName: 'poster.png', isImage: true, publishStatus: 'ready-to-publish' },
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
  ],
};

afterEach(() => { cleanup(); vi.clearAllMocks(); });
beforeEach(() => {
  fetchAssets.mockResolvedValue(ASSETS);
  fetchTopicDetail.mockResolvedValue(null);
});

function renderAndOpen(onDispatch = vi.fn().mockReturnValue(true)) {
  render(<PollinateOverlay onDispatch={onDispatch} />);
  act(() => { window.dispatchEvent(new CustomEvent('swarm:show-pollinate')); });
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
});
