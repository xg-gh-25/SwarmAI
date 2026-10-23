/**
 * Tests for uiContext serialization — agent proprioception (SENSE) wire boundary.
 *
 * The Gate-1 CRITICAL this guards: the old send path was a hand-picked object
 * literal that copied ONLY file_path/file_name, so any new field was silently
 * dropped. toEditorContextPayload is the SSOT that must carry canvas +
 * active_overlay whenever present, AND still degrade to the legacy 2-field shape
 * for a file-only snapshot. canvas-proprioception §3.1: every Canvas field reaches
 * the wire in snake_case, rows / commands are capped, empty parts are omitted.
 */
import { describe, it, expect } from 'vitest';
import { toEditorContextPayload, hasUiState, MAX_SNAPSHOT_OUTPUTS, type CanvasSnapshot } from '../uiContext';

const canvas = (p: Partial<CanvasSnapshot> = {}): CanvasSnapshot => ({
  open: true, visible: 'open', tab: 'artifacts', listFolded: false, pinned: false,
  file: null, pendingOpen: null, outputCount: 0, outputs: [], commands: [], ...p,
});

describe('toEditorContextPayload', () => {
  it('serializes the FULL snapshot incl. every Canvas field + active_overlay (no dropped fields)', () => {
    const p = toEditorContextPayload({
      filePath: 'designs/a.md',
      fileName: 'a.md',
      canvas: canvas({
        visible: 'rail', tab: 'changes', listFolded: true, pinned: true,
        file: { path: 'designs/a.md', name: 'a.md', openedBy: 'canvas', dirty: true, view: 'preview', review: true },
        pendingOpen: 'b.md',
        outputCount: 2,
        outputs: [{ path: 'r.md', kind: 'content', isNew: true }, { path: 's.md', isNew: false }],
        commands: [{ cmd: 'open-canvas-file', result: 'ignored', reason: 'file not found' }],
      }),
      activeOverlay: 'swarm:show-todo',
    });
    expect(p!.canvas).toEqual({
      open: true, visible: 'rail', tab: 'changes', list_folded: true, pinned: true, output_count: 2,
      outputs: [{ path: 'r.md', kind: 'content', new: true }, { path: 's.md', new: false }],
      file: { path: 'designs/a.md', name: 'a.md', opened_by: 'canvas', dirty: true, view: 'preview', review: true },
      pending_open: 'b.md',
      commands: [{ cmd: 'open-canvas-file', result: 'ignored', reason: 'file not found' }],
    });
    expect(p!.active_overlay).toBe('swarm:show-todo');
  });

  it('caps the output rows sent', () => {
    const outputs = Array.from({ length: 20 }, (_, i) => ({ path: `f${i}.md`, isNew: false }));
    const p = toEditorContextPayload({ canvas: canvas({ outputCount: 20, outputs }) });
    expect(p!.canvas!.outputs).toHaveLength(MAX_SNAPSHOT_OUTPUTS);
    expect(p!.canvas!.output_count).toBe(20);
  });

  it('degrades a file-only snapshot to the legacy 2-field payload (backward-compat)', () => {
    const p = toEditorContextPayload({ filePath: '/ws/a.md', fileName: 'a.md' });
    expect(p).toEqual({ file_path: '/ws/a.md', file_name: 'a.md' });
  });

  it('serializes a canvas-only snapshot (Canvas open, no file) without empty parts', () => {
    const p = toEditorContextPayload({ canvas: canvas() });
    expect(p!.file_path).toBe('');
    expect(p!.canvas!.file).toBeUndefined();
    expect(p!.canvas!.commands).toBeUndefined();
    expect(p!.canvas!.pending_open).toBeUndefined();
  });

  it('a closed Canvas with only a command result is still sent (the agent must learn it)', () => {
    const p = toEditorContextPayload({ canvas: canvas({ open: false, visible: 'hidden', commands: [{ cmd: 'open-canvas', result: 'pending' }] }) });
    expect(p!.canvas!.commands).toEqual([{ cmd: 'open-canvas', result: 'pending' }]);
  });

  it('serializes an overlay-only snapshot', () => {
    const p = toEditorContextPayload({ activeOverlay: 'swarm:show-history' });
    expect(p!.active_overlay).toBe('swarm:show-history');
  });

  it('OMITS a closed/empty canvas (no stale count leaks to the wire)', () => {
    const p = toEditorContextPayload({
      filePath: '/ws/a.md', fileName: 'a.md',
      canvas: canvas({ open: false, visible: 'hidden' }), activeOverlay: null,
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
    expect(hasUiState({ canvas: canvas() })).toBe(true);
  });
  it('false for empty / all-default', () => {
    expect(hasUiState(null)).toBe(false);
    expect(hasUiState({})).toBe(false);
    expect(hasUiState({ canvas: canvas({ open: false, visible: 'hidden' }) })).toBe(false);
  });
});
