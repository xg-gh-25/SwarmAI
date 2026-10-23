/**
 * useCanvasHost — unsaved edits guard the shown file (super-canvas-fixing.md C1).
 *
 * - While the shown file is dirty, a USER open of another file does not switch: it
 *   waits in `pendingOpen`; resolvePending(true) opens it and clears dirty,
 *   resolvePending(false) keeps the file and drops the held open.
 * - An AUTO open (auto-surface / default selection) never replaces a dirty file and
 *   never creates a prompt.
 * - Re-opening the SAME path while dirty is not a switch (no prompt).
 * - Editing an auto-opened file clears its `auto` flag (it is now the user's).
 * - close() clears dirty and any held open.
 *
 * Drives the real swarm:open-file document event + the mocked /resolve.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { useCanvasHost } from '../useCanvasHost';

vi.mock('../../services/api', () => ({
  default: { get: vi.fn(async (_url: string, opts?: { params?: { path?: string } }) => ({ data: { resolved_path: opts?.params?.path ?? '' } })) },
}));
vi.mock('../../services/canvasSession', () => ({
  refreshCanvasSession: async () => {},
  setActiveCanvasSession: () => {},
  useCanvasSessionArtifacts: () => undefined,
  cachedArtifacts: () => undefined,
  sessionForTab: () => null,
}));

async function open(path: string, extra: Record<string, unknown> = {}) {
  await act(async () => {
    document.dispatchEvent(new CustomEvent('swarm:open-file', { detail: { path, ...extra } }));
    await Promise.resolve(); await Promise.resolve();
  });
}

function host() {
  return renderHook(() => useCanvasHost({ activeTabId: 'tab-A', sessionId: 's-A', isStreaming: false }));
}

beforeEach(() => { sessionStorage.clear(); });

describe('dirty file guard', () => {
  it('a user open while dirty waits; discard opens it, keep stays', async () => {
    const { result } = host();
    await open('/ws/a.md');
    await waitFor(() => expect(result.current.file?.filePath).toBe('/ws/a.md'));
    act(() => result.current.setFileDirty(true));
    await open('/ws/b.md');
    expect(result.current.file?.filePath).toBe('/ws/a.md');
    expect(result.current.pendingOpen?.filePath).toBe('/ws/b.md');

    act(() => result.current.resolvePending(false));
    expect(result.current.file?.filePath).toBe('/ws/a.md');
    expect(result.current.pendingOpen).toBeNull();
    expect(result.current.fileDirty).toBe(true);

    await open('/ws/b.md');
    act(() => result.current.resolvePending(true));
    expect(result.current.file?.filePath).toBe('/ws/b.md');
    expect(result.current.pendingOpen).toBeNull();
    expect(result.current.fileDirty).toBe(false);
  });

  it('an auto open never replaces a dirty file and never prompts', async () => {
    const { result } = host();
    await open('/ws/a.md');
    await waitFor(() => expect(result.current.file?.filePath).toBe('/ws/a.md'));
    act(() => result.current.setFileDirty(true));
    await open('/ws/new-output.md', { auto: true });
    expect(result.current.file?.filePath).toBe('/ws/a.md');
    expect(result.current.pendingOpen).toBeNull();
  });

  it('re-opening the same path while dirty is not a switch', async () => {
    const { result } = host();
    await open('/ws/a.md');
    await waitFor(() => expect(result.current.file?.filePath).toBe('/ws/a.md'));
    act(() => result.current.setFileDirty(true));
    await open('/ws/a.md');
    expect(result.current.pendingOpen).toBeNull();
  });

  it('editing an auto-opened file makes it the user’s', async () => {
    const { result } = host();
    await open('/ws/a.md', { auto: true });
    await waitFor(() => expect(result.current.file?.auto).toBe(true));
    act(() => result.current.setFileDirty(true));
    expect(result.current.file?.auto).toBeUndefined();
    expect(result.current.file?.filePath).toBe('/ws/a.md');
  });

  it('close clears dirty and the held open', async () => {
    const { result } = host();
    await open('/ws/a.md');
    await waitFor(() => expect(result.current.file).not.toBeNull());
    act(() => result.current.setFileDirty(true));
    await open('/ws/b.md');
    act(() => result.current.close());
    expect(result.current.fileDirty).toBe(false);
    expect(result.current.pendingOpen).toBeNull();
  });
});
