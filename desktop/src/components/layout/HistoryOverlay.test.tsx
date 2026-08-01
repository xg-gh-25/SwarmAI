/**
 * Tests for HistoryOverlay — the left-nav History surface.
 *
 * Covers:
 * - AC1/AC2: opens on `swarm:show-history`, renders the session list (HistoryView),
 *   clicking a session calls onSelectSession + closes.
 * - AC5: typing debounces → calls searchService.searchSessions and renders the
 *   FTS results; clearing the query falls back to the grouped session list.
 */
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, screen, cleanup, act, fireEvent } from '@testing-library/react';

/** Advance past the debounce AND flush the resolved-promise microtask queue,
 *  without waitFor (which polls on real timers and deadlocks under fake timers). */
async function flushDebounce() {
  await act(async () => {
    vi.advanceTimersByTime(300);
    // let the awaited searchSessions promise + setState settle
    await Promise.resolve();
    await Promise.resolve();
  });
}

// i18n: make t() return the key (HistoryView uses useTranslation)
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

const searchSessions = vi.fn();
vi.mock('../../services/search', () => ({
  searchService: { searchSessions: (q: string) => searchSessions(q) },
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
  const onSelectSession = vi.fn();
  const onDeleteSession = vi.fn();
  render(
    <HistoryOverlay
      groupedSessions={groupSessionsByTime(SESSIONS)}
      agents={AGENTS}
      onSelectSession={onSelectSession}
      onDeleteSession={onDeleteSession}
      {...overrides}
    />,
  );
  return { onSelectSession, onDeleteSession };
}

describe('HistoryOverlay', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    searchSessions.mockReset();
  });
  afterEach(() => {
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
    cleanup();
  });

  it('is closed until swarm:show-history fires, then shows the grouped list', () => {
    renderOverlay();
    expect(screen.queryByTestId('history-overlay')).toBeNull();

    act(() => window.dispatchEvent(new CustomEvent('swarm:show-history')));

    expect(screen.getByTestId('history-overlay')).toBeInTheDocument();
    // grouped fallback shows both sessions (no query yet)
    expect(screen.getByText('Kubernetes chat')).toBeInTheDocument();
    expect(screen.getByText('Docker notes')).toBeInTheDocument();
  });

  it('clicking a session calls onSelectSession', () => {
    const { onSelectSession } = renderOverlay();
    act(() => window.dispatchEvent(new CustomEvent('swarm:show-history')));

    fireEvent.click(screen.getByText('Kubernetes chat'));
    expect(onSelectSession).toHaveBeenCalledWith(expect.objectContaining({ id: 's1' }));
  });

  it('debounces then calls searchSessions and renders FTS results (AC5)', async () => {
    // FTS result is a session whose TITLE would not match the query — proving
    // the rendered list comes from the backend, not the client title filter.
    searchSessions.mockResolvedValue([mkSession('s9', 'Untitled')]);
    renderOverlay();
    act(() => window.dispatchEvent(new CustomEvent('swarm:show-history')));

    const input = screen.getByPlaceholderText('Search conversations…');
    act(() => fireEvent.change(input, { target: { value: 'ingress' } }));

    // not called before debounce elapses
    expect(searchSessions).not.toHaveBeenCalled();
    await flushDebounce();
    expect(searchSessions).toHaveBeenCalledWith('ingress');
    expect(screen.getByText('Untitled')).toBeInTheDocument();
  });

  it('shows a Searching… hint on the first in-flight query (no grouped flash)', async () => {
    // A never-resolving search → results stay null while in flight.
    searchSessions.mockReturnValue(new Promise(() => {}));
    renderOverlay();
    act(() => window.dispatchEvent(new CustomEvent('swarm:show-history')));
    const input = screen.getByPlaceholderText('Search conversations…');

    act(() => fireEvent.change(input, { target: { value: 'ingress' } }));
    await act(async () => {
      vi.advanceTimersByTime(300);
      await Promise.resolve();
    });

    // The grouped fallback session must NOT be showing; the hint must be.
    expect(screen.queryByText('Kubernetes chat')).toBeNull();
    expect(screen.getByText('Searching…')).toBeInTheDocument();
  });

  it('clearing while a search is in flight does NOT commit stale results', async () => {
    // Deferred search: we resolve it manually AFTER the query is cleared, to
    // reproduce the clear-to-empty-while-in-flight race.
    let resolveSearch: (v: ChatSession[]) => void = () => {};
    searchSessions.mockReturnValue(new Promise<ChatSession[]>((r) => { resolveSearch = r; }));
    renderOverlay();
    act(() => window.dispatchEvent(new CustomEvent('swarm:show-history')));
    const input = screen.getByPlaceholderText('Search conversations…');

    // fire the debounced search (fetch now in flight, unresolved)
    act(() => fireEvent.change(input, { target: { value: 'ingress' } }));
    await act(async () => { vi.advanceTimersByTime(300); await Promise.resolve(); });

    // clear the box BEFORE the fetch resolves → should fall back to grouped list
    act(() => fireEvent.change(input, { target: { value: '' } }));
    await act(async () => { vi.advanceTimersByTime(300); await Promise.resolve(); });

    // now the stale fetch resolves — its result must be IGNORED (seq bumped on clear)
    await act(async () => { resolveSearch([mkSession('s9', 'Untitled')]); await Promise.resolve(); });

    expect(screen.queryByText('Untitled')).toBeNull();          // stale result NOT shown
    expect(screen.getByText('Kubernetes chat')).toBeInTheDocument(); // grouped fallback stuck
  });

  it('clearing the query falls back to the grouped list (AC5)', async () => {
    searchSessions.mockResolvedValue([mkSession('s9', 'Untitled')]);
    renderOverlay();
    act(() => window.dispatchEvent(new CustomEvent('swarm:show-history')));
    const input = screen.getByPlaceholderText('Search conversations…');

    act(() => fireEvent.change(input, { target: { value: 'ingress' } }));
    await flushDebounce();
    expect(screen.getByText('Untitled')).toBeInTheDocument();

    // clear → back to grouped list, no new backend call needed
    act(() => fireEvent.change(input, { target: { value: '' } }));
    await flushDebounce();
    expect(screen.getByText('Kubernetes chat')).toBeInTheDocument();
  });
});
