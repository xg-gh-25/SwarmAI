/**
 * Tests for TerminalContext — the React bridge over TerminalStore + panel state.
 *
 * Covers:
 *   - AC5: panelOpen toggles and persists to localStorage (mirrors LayoutContext).
 *   - tabs reflect the store; openTerminal opens the panel (so a right-click
 *     "open terminal here" makes the panel visible).
 *   - activeTabId tracks the newest opened tab.
 *
 * Store's pty service is mocked (boundary); context + store are real.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import type { ReactNode } from 'react';

let spawnCount = 0;
vi.mock('../services/pty', () => ({
  spawn: () => ({
    pid: ++spawnCount,
    onData: vi.fn(() => ({ dispose: vi.fn() })),
    onExit: vi.fn(() => ({ dispose: vi.fn() })),
    write: vi.fn(),
    resize: vi.fn(),
    kill: vi.fn(),
  }),
}));

import { TerminalProvider, useTerminal } from './TerminalContext';
import { terminalStore } from '../stores/TerminalStore';

const wrapper = ({ children }: { children: ReactNode }) => (
  <TerminalProvider>{children}</TerminalProvider>
);

describe('TerminalContext', () => {
  beforeEach(() => {
    spawnCount = 0;
    localStorage.clear();
    terminalStore.clear();
  });

  it('AC5: panelOpen defaults false and togglePanel flips + persists', () => {
    const { result } = renderHook(() => useTerminal(), { wrapper });
    expect(result.current.panelOpen).toBe(false);

    act(() => result.current.togglePanel());
    expect(result.current.panelOpen).toBe(true);
    expect(localStorage.getItem('terminalPanelOpen')).toBe('true');

    act(() => result.current.togglePanel());
    expect(result.current.panelOpen).toBe(false);
    expect(localStorage.getItem('terminalPanelOpen')).toBe('false');
  });

  it('openTerminal adds a tab, makes it active, and opens the panel', () => {
    const { result } = renderHook(() => useTerminal(), { wrapper });
    expect(result.current.tabs.length).toBe(0);

    let id = '';
    act(() => { id = result.current.openTerminal({ cwd: '/tmp/x' }); });

    expect(result.current.tabs.length).toBe(1);
    expect(result.current.activeTabId).toBe(id);
    expect(result.current.panelOpen).toBe(true); // opening a terminal reveals the panel
  });

  it('closeTerminal removes the tab and re-points active to a remaining tab', () => {
    const { result } = renderHook(() => useTerminal(), { wrapper });
    let a = '', b = '';
    act(() => { a = result.current.openTerminal({}); });
    act(() => { b = result.current.openTerminal({}); });
    expect(result.current.activeTabId).toBe(b);

    act(() => result.current.closeTerminal(b));
    expect(result.current.tabs.length).toBe(1);
    expect(result.current.activeTabId).toBe(a); // active falls back to remaining
  });

  it('AC5: badge count = number of open tabs (tabs.length)', () => {
    const { result } = renderHook(() => useTerminal(), { wrapper });
    act(() => { result.current.openTerminal({}); });
    act(() => { result.current.openTerminal({}); });
    expect(result.current.tabs.length).toBe(2);
    act(() => { result.current.closeTerminal(result.current.tabs[0].id); });
    expect(result.current.tabs.length).toBe(1);
  });

  it('persisted panelOpen=true is restored on mount', () => {
    localStorage.setItem('terminalPanelOpen', 'true');
    const { result } = renderHook(() => useTerminal(), { wrapper });
    expect(result.current.panelOpen).toBe(true);
  });
});
