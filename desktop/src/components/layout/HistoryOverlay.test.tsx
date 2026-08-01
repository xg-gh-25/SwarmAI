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

async function flushMicro() {
  await act(async () => {
    vi.advanceTimersByTime(300);
    await Promise.resolve();
    await Promise.resolve();
  });
}

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
vi.mock('../../services/chat', () => ({
  chatService: { getSessionMessagesPaginated: (id: string, limit?: number) => getSessionMessagesPaginated(id, limit) },
}));

import { HistoryOverlay } from './HistoryOverlay';
import type { ChatSession, Agent } from '../../types';
import { groupSessionsByTime } from '../../pages/chat/utils';

const AGENTS: Agent[] = [{ id: 'a1', name: 'Swarm' } as Agent];

function mkSession(id: string, title: string): ChatSession {
  const now = new Date().toISOString();
  return { id, agentId: 'a1', title, createdAt: now, lastAccessedAt: now };
}

const SESSIONS = [mkSession('s1', 'Kubernetes chat'), mkSession('s2', 'Docker notes')];

function renderOverlay(overrides: Partial<Parameters<typeof HistoryOverlay>[0]> = {}) {
  const onResume = vi.fn().mockReturnValue(true);
  const onDeleteSession = vi.fn();
  render(
    <HistoryOverlay
      groupedSessions={groupSessionsByTime(SESSIONS)}
      agents={AGENTS}
      onResume={onResume}
      onDeleteSession={onDeleteSession}
      {...overrides}
    />,
  );
  return { onResume, onDeleteSession };
}

describe('HistoryOverlay (方案 B — read-only browser)', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    searchSessions.mockReset();
    getSessionMessagesPaginated.mockReset();
    getSessionMessagesPaginated.mockResolvedValue([]);
  });
  afterEach(() => {
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
    cleanup();
  });

  it('is closed until swarm:show-history fires, then shows the grouped list + empty preview', () => {
    renderOverlay();
    expect(screen.queryByTestId('history-overlay')).toBeNull();

    act(() => window.dispatchEvent(new CustomEvent('swarm:show-history')));

    expect(screen.getByTestId('history-overlay')).toBeInTheDocument();
    expect(screen.getByText('Kubernetes chat')).toBeInTheDocument();
    // preview pane starts empty
    expect(screen.getByText(/select a conversation to preview/i)).toBeInTheDocument();
  });

  it('clicking a row PREVIEWS in-place — does NOT resume or close', async () => {
    getSessionMessagesPaginated.mockResolvedValue([
      { id: 'm1', role: 'user', content: [{ type: 'text', text: 'hello world' }], createdAt: new Date().toISOString() },
    ]);
    const { onResume } = renderOverlay();
    act(() => window.dispatchEvent(new CustomEvent('swarm:show-history')));

    act(() => fireEvent.click(screen.getByText('Kubernetes chat')));
    // preview fetch uses the shared tab-load limit (200)
    expect(getSessionMessagesPaginated).toHaveBeenCalledWith('s1', 200);
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    // resume NOT triggered by a row click; overlay still open
    expect(onResume).not.toHaveBeenCalled();
    expect(screen.getByTestId('history-overlay')).toBeInTheDocument();
    expect(screen.getByText('Read-only preview')).toBeInTheDocument();
  });

  it('Resume in tab calls onResume and closes when it lands (true)', async () => {
    const onResume = vi.fn().mockReturnValue(true);
    renderOverlay({ onResume });
    act(() => window.dispatchEvent(new CustomEvent('swarm:show-history')));
    act(() => fireEvent.click(screen.getByText('Kubernetes chat')));
    await act(async () => { await Promise.resolve(); });

    act(() => fireEvent.click(screen.getByText(/resume in tab/i)));
    expect(onResume).toHaveBeenCalledWith(expect.objectContaining({ id: 's1' }));
    // landed → close() called → Modal plays its exit transition then unmounts
    await act(async () => { vi.advanceTimersByTime(400); await Promise.resolve(); });
    expect(screen.queryByTestId('history-overlay')).toBeNull();
  });

  it('Resume stays OPEN when onResume returns false (all tabs busy)', async () => {
    renderOverlay({ onResume: vi.fn().mockReturnValue(false) as any });
    act(() => window.dispatchEvent(new CustomEvent('swarm:show-history')));
    act(() => fireEvent.click(screen.getByText('Kubernetes chat')));
    await act(async () => { await Promise.resolve(); });

    act(() => fireEvent.click(screen.getByText(/resume in tab/i)));
    // busy → overlay stays open
    expect(screen.getByTestId('history-overlay')).toBeInTheDocument();
  });

  it('debounces then calls searchSessions and renders FTS results', async () => {
    searchSessions.mockResolvedValue([mkSession('s9', 'Untitled')]);
    renderOverlay();
    act(() => window.dispatchEvent(new CustomEvent('swarm:show-history')));

    const input = screen.getByPlaceholderText('Search conversations…');
    act(() => fireEvent.change(input, { target: { value: 'ingress' } }));
    expect(searchSessions).not.toHaveBeenCalled();
    await flushMicro();
    expect(searchSessions).toHaveBeenCalledWith('ingress');
    expect(screen.getByText('Untitled')).toBeInTheDocument();
  });
});
