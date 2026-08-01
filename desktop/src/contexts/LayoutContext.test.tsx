/**
 * Tests for LayoutContext — the LIVE surface after the A10 redesign
 * (run_1aab916c, Gate-2 E-3): workspace scope + validation, modal management,
 * settings-tab deep-link, and workspace-settings id.
 *
 * The former workspace-explorer collapse/width/resize/narrow-viewport machinery
 * was deleted (the explorer is overlay-only and fills its parent) — so the old
 * property suite that exercised it was retired with the behavior.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { ReactNode } from 'react';
import { LayoutProvider, useLayout } from './LayoutContext';

class MockLocalStorage {
  private store = new Map<string, string>();
  getItem(k: string) { return this.store.get(k) ?? null; }
  setItem(k: string, v: string) { this.store.set(k, v); }
  removeItem(k: string) { this.store.delete(k); }
  clear() { this.store.clear(); }
  get length() { return this.store.size; }
  key(i: number) { return Array.from(this.store.keys())[i] ?? null; }
}

let originalLocalStorage: Storage;
let mockStorage: MockLocalStorage;

const wrapper = ({ children }: { children: ReactNode }) => (
  <LayoutProvider>{children}</LayoutProvider>
);

beforeEach(() => {
  originalLocalStorage = globalThis.localStorage;
  mockStorage = new MockLocalStorage();
  Object.defineProperty(globalThis, 'localStorage', { value: mockStorage, configurable: true, writable: true });
});

afterEach(() => {
  Object.defineProperty(globalThis, 'localStorage', { value: originalLocalStorage, configurable: true, writable: true });
});

describe('LayoutContext — workspace scope', () => {
  it('defaults to "all" and persists a scope change to localStorage', () => {
    const { result } = renderHook(() => useLayout(), { wrapper });
    expect(result.current.selectedWorkspaceScope).toBe('all');

    act(() => result.current.setSelectedWorkspaceScope('ws-123'));
    expect(result.current.selectedWorkspaceScope).toBe('ws-123');
    expect(mockStorage.getItem('lastWorkspaceScope')).toBe('ws-123');
  });

  it('validateWorkspaceScope resets to "all" when the stored scope no longer exists', () => {
    const { result } = renderHook(() => useLayout(), { wrapper });
    act(() => result.current.setSelectedWorkspaceScope('ws-gone'));
    act(() => result.current.validateWorkspaceScope(['ws-a', 'ws-b']));
    expect(result.current.selectedWorkspaceScope).toBe('all');
  });

  it('validateWorkspaceScope keeps a scope that still exists', () => {
    const { result } = renderHook(() => useLayout(), { wrapper });
    act(() => result.current.setSelectedWorkspaceScope('ws-a'));
    act(() => result.current.validateWorkspaceScope(['ws-a', 'ws-b']));
    expect(result.current.selectedWorkspaceScope).toBe('ws-a');
  });
});

describe('LayoutContext — modal management', () => {
  it('opens and closes the active modal', () => {
    const { result } = renderHook(() => useLayout(), { wrapper });
    expect(result.current.activeModal).toBeNull();

    act(() => result.current.openModal('settings'));
    expect(result.current.activeModal).toBe('settings');

    act(() => result.current.closeModal());
    expect(result.current.activeModal).toBeNull();
  });
});

describe('LayoutContext — settings deep-link + workspace-settings id', () => {
  it('stores the settings-tab deep-link target', () => {
    const { result } = renderHook(() => useLayout(), { wrapper });
    expect(result.current.settingsTab).toBeUndefined();
    act(() => result.current.setSettingsTab('skills'));
    expect(result.current.settingsTab).toBe('skills');
  });

  it('stores the workspace-settings id', () => {
    const { result } = renderHook(() => useLayout(), { wrapper });
    act(() => result.current.setWorkspaceSettingsId('ws-42'));
    expect(result.current.workspaceSettingsId).toBe('ws-42');
  });
});
