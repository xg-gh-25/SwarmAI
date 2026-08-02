/**
 * Tests for uiContext serialization — agent proprioception (SENSE) wire boundary.
 *
 * The Gate-1 CRITICAL this guards: the old send path was a hand-picked object
 * literal that copied ONLY file_path/file_name, so any new field was silently
 * dropped. toEditorContextPayload is the SSOT that must carry canvas +
 * active_overlay whenever present, AND still degrade to the legacy 2-field shape
 * for a file-only snapshot.
 */
import { describe, it, expect } from 'vitest';
import { toEditorContextPayload, hasUiState } from '../uiContext';

describe('toEditorContextPayload', () => {
  it('serializes the FULL snapshot incl. canvas + active_overlay (no dropped fields)', () => {
    const p = toEditorContextPayload({
      filePath: '/ws/report.html',
      fileName: 'report.html',
      canvas: { open: true, outputCount: 2, pinned: true, muted: false, collapsed: false },
      activeOverlay: 'swarm:show-todo',
    });
    expect(p).not.toBeNull();
    expect(p!.file_path).toBe('/ws/report.html');
    expect(p!.canvas).toEqual({
      open: true, output_count: 2, pinned: true, muted: false, collapsed: false,
    });
    expect(p!.active_overlay).toBe('swarm:show-todo');
  });

  it('degrades a file-only snapshot to the legacy 2-field payload (backward-compat)', () => {
    const p = toEditorContextPayload({ filePath: '/ws/a.md', fileName: 'a.md' });
    expect(p).toEqual({ file_path: '/ws/a.md', file_name: 'a.md' });
    expect(p!.canvas).toBeUndefined();
    expect(p!.active_overlay).toBeUndefined();
  });

  it('serializes a canvas-only snapshot (Canvas open, no file)', () => {
    const p = toEditorContextPayload({
      canvas: { open: true, outputCount: 0, pinned: false, muted: false, collapsed: true },
    });
    expect(p).not.toBeNull();
    expect(p!.file_path).toBe('');
    expect(p!.canvas!.collapsed).toBe(true);
  });

  it('serializes an overlay-only snapshot', () => {
    const p = toEditorContextPayload({ activeOverlay: 'swarm:show-history' });
    expect(p).not.toBeNull();
    expect(p!.active_overlay).toBe('swarm:show-history');
  });

  it('OMITS a closed/empty canvas (no stale count leaks to the wire)', () => {
    // Canvas closed → all-default snapshot → canvas key must NOT appear.
    const p = toEditorContextPayload({
      filePath: '/ws/a.md',
      fileName: 'a.md',
      canvas: { open: false, outputCount: 0, pinned: false, muted: false, collapsed: false },
      activeOverlay: null,
    });
    expect(p!.canvas).toBeUndefined();
    expect(p!.active_overlay).toBeUndefined();
  });

  it('returns null for an empty snapshot (caller omits editor_context)', () => {
    expect(toEditorContextPayload(null)).toBeNull();
    expect(toEditorContextPayload(undefined)).toBeNull();
    expect(toEditorContextPayload({})).toBeNull();
    expect(toEditorContextPayload({ filePath: '', fileName: '' })).toBeNull();
  });
});

describe('hasUiState', () => {
  it('true when any of file / canvas-state / overlay present', () => {
    expect(hasUiState({ filePath: '/x' })).toBe(true);
    expect(hasUiState({ activeOverlay: 'swarm:show-jobs' })).toBe(true);
    expect(hasUiState({ canvas: { open: true, outputCount: 0, pinned: false, muted: false, collapsed: false } })).toBe(true);
  });
  it('false for empty / all-default', () => {
    expect(hasUiState(null)).toBe(false);
    expect(hasUiState({})).toBe(false);
    expect(hasUiState({ canvas: { open: false, outputCount: 0, pinned: false, muted: false, collapsed: false } })).toBe(false);
  });
});
