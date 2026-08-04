/**
 * Tests for HistoryOverlay — the read-only History browser (方案 B).
 *
 * Covers:
 * - opens on `swarm:show-history`, renders the session list.
 * - debounced content search (searchService.searchSessions) + empty-query fallback.
 * - clicking a row PREVIEWS in-place (does NOT resume/close); loads messages read-only.
 * - "Resume in tab" calls onResume; overlay closes only when it returns true
 *   (all-busy → false → stays open).
 */
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, screen, cleanup, act, fireEvent } from '@testing-library/react';

// jsdom lacks ResizeObserver; MessageBubble → UserMessageView uses it.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

const searchSessions = vi.fn();
vi.mock('../../services/search', () => ({
  searchService: { searchSessions: (q: string) => searchSessions(q) },
}));

const getSessionMessagesPaginated = vi.fn();
const listSessions = vi.fn();
vi.mock('../../services/chat', () => ({
  chatService: {
    getSessionMessagesPaginated: (id: string, limit?: number) => getSessionMessagesPaginated(id, limit),
    listSessions: (agentId?: string) => listSessions(agentId),
  },
}));
vi.mock('../../services/agents', () => ({
  agentsService: { list: () => Promise.resolve([{ id: 'a1', name: 'Swarm' }]) },
}));

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { HistoryContent } from './HistoryOverlay';
import type { ChatSession } from '../../types';

function mkSession(id: string, title: string): ChatSession {
  const now = new Date().toISOString();
  return { id, agentId: 'a1', title, createdAt: now, lastAccessedAt: now };
}

const SESSIONS = [mkSession('s1', 'Kubernetes chat'), mkSession('s2', 'Docker notes')];

// M3: HistoryOverlay → HistoryContent (OverlayHost registry). Content self-fetches
// sessions/agents from the query cache (mocked here) and takes handlers + agentId via
// props (was the ctx bridge). It renders immediately (host owns open) — no show-event.
function renderOverlay(overrides: { onResume?: () => boolean } = {}) {
  const onResume = vi.fn().mockReturnValue(overrides.onResume ? overrides.onResume() : true);
  const onDeleteSession = vi.fn();
  listSessions.mockResolvedValue(SESSIONS);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <HistoryContent agentId="a1" onResume={onResume} onDeleteSession={onDeleteSession} close={() => {}} />
    </QueryClientProvider>,
  );
  return { onResume, onDeleteSession };
}

describe('HistoryOverlay (方案 B — read-only browser)', () => {
  // Real timers (was fake): HistoryContent self-fetches sessions via TanStack Query,
  // whose async resolution does not advance under fake timers. `findBy*` polls real
  // time for the async list; the search debounce (250ms) is waited on directly.
  beforeEach(() => {
    searchSessions.mockReset();
    getSessionMessagesPaginated.mockReset();
    getSessionMessagesPaginated.mockResolvedValue([]);
  });
  afterEach(() => {
    cleanup();
  });

  it('renders immediately (host-owned open), shows the self-fetched grouped list + empty preview', async () => {
    renderOverlay();
    // History content renders at once (host owns open); the session list is
    // self-fetched (mocked listSessions) → findBy polls until it lands.
    expect(screen.getByTestId('history-overlay')).toBeInTheDocument();
    expect(await screen.findByText('Kubernetes chat')).toBeInTheDocument();
    // preview pane starts empty
    expect(screen.getByText(/select a conversation to preview/i)).toBeInTheDocument();
  });

  it('clicking a row PREVIEWS in-place — does NOT resume or close', async () => {
    getSessionMessagesPaginated.mockResolvedValue([
      { id: 'm1', role: 'user', content: [{ type: 'text', text: 'hello world' }], createdAt: new Date().toISOString() },
    ]);
    const { onResume } = renderOverlay();
    const row = await screen.findByText('Kubernetes chat'); // self-fetched list
    fireEvent.click(row);
    // preview fetch uses the shared tab-load limit (200)
    expect(getSessionMessagesPaginated).toHaveBeenCalledWith('s1', 200);

    // resume NOT triggered by a row click; overlay still open + preview shown
    expect(onResume).not.toHaveBeenCalled();
    expect(screen.getByTestId('history-overlay')).toBeInTheDocument();
    expect(await screen.findByText('Read-only preview')).toBeInTheDocument();
  });

  it('Resume in tab calls onResume and closes when it lands (true)', async () => {
    const close = vi.fn();
    const onResume = vi.fn().mockReturnValue(true);
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <HistoryContent agentId="a1" onResume={onResume} onDeleteSession={vi.fn()} close={close} />
      </QueryClientProvider>,
    );
    fireEvent.click(await screen.findByText('Kubernetes chat'));
    fireEvent.click(await screen.findByText(/resume in tab/i));
    expect(onResume).toHaveBeenCalledWith(expect.objectContaining({ id: 's1' }));
    // landed → the host's close() is called (host then unmounts the surface).
    expect(close).toHaveBeenCalledTimes(1);
  });

  it('does NOT close when onResume returns false (all tabs busy)', async () => {
    const close = vi.fn();
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <HistoryContent agentId="a1" onResume={() => false} onDeleteSession={vi.fn()} close={close} />
      </QueryClientProvider>,
    );
    fireEvent.click(await screen.findByText('Kubernetes chat'));
    fireEvent.click(await screen.findByText(/resume in tab/i));
    // busy → must NOT close; overlay stays open
    expect(close).not.toHaveBeenCalled();
    expect(screen.getByTestId('history-overlay')).toBeInTheDocument();
  });

  it('debounces then calls searchSessions and renders FTS results', async () => {
    searchSessions.mockResolvedValue([mkSession('s9', 'Untitled')]);
    renderOverlay();
    await screen.findByText('Kubernetes chat'); // list loaded

    const input = screen.getByPlaceholderText('Search conversations…');
    fireEvent.change(input, { target: { value: 'ingress' } });
    expect(searchSessions).not.toHaveBeenCalled();
    // real 250ms debounce → findBy polls for the result
    expect(await screen.findByText('Untitled')).toBeInTheDocument();
    expect(searchSessions).toHaveBeenCalledWith('ingress');
  });
});
