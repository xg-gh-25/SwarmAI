/**
 * The Canvas host and a versions document (utils/fileVersions): swarm:open-file-versions
 * shows it on the active tab with its text and nothing resolved; swarm:open-file (the
 * agent's open-canvas-file too) never carries versions, whatever its detail says; the
 * event is not an agent's ui_command.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useCanvasHost } from '../../../hooks/useCanvasHost';
import { openFileVersions, OPEN_FILE_VERSIONS_EVENT } from '../utils/fileVersions';
import { UI_COMMAND_TABLE, dispatchUiCommand } from '../../../utils/uiCommands';

vi.mock('../../../services/api', () => ({
  default: { get: vi.fn(async (_url: string, opts?: { params?: { path?: string } }) => ({ data: { resolved_path: opts?.params?.path ?? '' } })) },
}));
vi.mock('../../../services/canvasSession', () => ({
  refreshCanvasSession: async () => {},
  refreshCanvasArtifacts: async () => {},
  setCanvasStreaming: async () => {},
  setActiveCanvasSession: () => {},
  useCanvasSessionArtifacts: () => undefined,
  cachedArtifacts: () => undefined,
  sessionForTab: () => null,
}));

beforeEach(() => { sessionStorage.clear(); vi.clearAllMocks(); });

describe('a versions document in the Canvas', () => {
  it('opens on the active tab with its text, resolving nothing', async () => {
    const api = (await import('../../../services/api')).default;
    const { result } = renderHook(() => useCanvasHost({ activeTabId: 'tab-A', sessionId: 's-A', isStreaming: false }));
    await act(async () => { openFileVersions({ source: 'hive:so_x@bbbbbbbbbbbb', path: 'src/a.py', base: 'a = 1\n', tip: null }); });
    expect(result.current.file).toMatchObject({ filePath: 'hive:so_x@bbbbbbbbbbbb/src/a.py', fileName: 'a.py',
      versions: { base: 'a = 1\n', tip: null } });
    expect(result.current.isOpen).toBe(true);
    expect(vi.mocked(api.get).mock.calls.filter(([u]) => String(u).includes('/resolve'))).toEqual([]);
  });

  it('an open-file (a user\'s or an agent\'s) never carries versions', async () => {
    const { result } = renderHook(() => useCanvasHost({ activeTabId: 'tab-A', sessionId: 's-A', isStreaming: false }));
    await act(async () => {
      document.dispatchEvent(new CustomEvent('swarm:open-file', { detail: { path: 'src/a.py', versions: { base: 'x', tip: 'y' } } }));
      await Promise.resolve(); await Promise.resolve();
    });
    expect(result.current.file?.filePath).toBe('src/a.py');
    expect(result.current.file?.versions).toBeUndefined();
    expect(Object.values(UI_COMMAND_TABLE).map((e) => e.event)).not.toContain(OPEN_FILE_VERSIONS_EVENT);
    expect(dispatchUiCommand('open-file-versions', 'src/a.py')).toBe(false);
  });
});
