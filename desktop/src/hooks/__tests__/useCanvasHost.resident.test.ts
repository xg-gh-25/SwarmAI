/**
 * useCanvasHost — RESIDENT output-rail store (run_9e42c066).
 *
 * The bug this pins: the Canvas output rail (useReferencedFiles + its sole
 * swarm:file-changed listener) used to mount ONLY inside FileViewerPanel, which
 * renders only when canvas.isOpen. So a pipeline-finish `source-final` batch
 * arriving while Canvas is CLOSED reached no listener → rows lost → "跑完看不到".
 *
 * The fix lifts useReferencedFiles into useCanvasHost (always mounted in ChatPage),
 * making it the SOLE listener + the SOLE outputCount source. These tests drive the
 * REAL swarm:file-changed event (the production signal), not the removed
 * onCanvasMeta round-trip — so they exercise the actual capture path.
 *
 * Mutation check: revert useCanvasHost to not call useReferencedFiles (or gate the
 * listener on isOpen) → the "captured while closed" test goes RED.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import type { Mock } from 'vitest';
import { useCanvasHost } from '../useCanvasHost';
import api from '../../services/api';
import { resetCanvasSession } from '../../services/canvasSession';
import { installFakeCanvasBackend, settleCanvas } from '../../test-utils/fakeCanvasBackend';

vi.mock('../../services/api', () => ({
  default: { get: vi.fn().mockResolvedValue({ data: { resolved_path: 'resolved.md' } }) },
}));

function fileChanged(detail: {
  path: string;
  tabId: string;
  kind?: string;
  operation?: string;
  relevance?: string;
}) {
  window.dispatchEvent(
    new CustomEvent('swarm:file-changed', {
      detail: { operation: 'written', relevance: 'deliverable', ...detail },
    }),
  );
}

describe('useCanvasHost — resident output-rail store (capture while CLOSED)', () => {
  let fake: ReturnType<typeof installFakeCanvasBackend>;
  beforeEach(() => {
    vi.clearAllMocks();
    resetCanvasSession();
    fake = installFakeCanvasBackend(api.get as Mock);
  });
  afterEach(() => fake.uninstall());

  it('captures a source-final write that arrives while Canvas is CLOSED', async () => {
    const { result } = renderHook(() =>
      useCanvasHost({ activeTabId: 'A', sessionId: 's-A', isStreaming: false }),
    );
    // Canvas is closed (no file, not manuallyOpen).
    expect(result.current.isOpen).toBe(false);

    // A pipeline-finish batch arrives for tab A while closed.
    act(() => {
      fileChanged({ path: 'backend/foo.py', tabId: 'A', kind: 'source-final' });
      fileChanged({ path: 'backend/bar.py', tabId: 'A', kind: 'source-final' });
    });

    await settleCanvas();
    // The resident store captured both, and outputCount reflects them —
    // even though the panel (and its old listener) never mounted.
    expect(result.current.outputCount).toBe(2);
    expect(result.current.referencedFiles.written.map((f) => f.path).sort()).toEqual([
      'backend/bar.py',
      'backend/foo.py',
    ]);
  });

  it('outputCount excludes process/source machine-noise (SSOT parity with the rail)', async () => {
    const { result } = renderHook(() =>
      useCanvasHost({ activeTabId: 'A', sessionId: 's-A', isStreaming: false }),
    );
    act(() => {
      fileChanged({ path: 'a.md', tabId: 'A', kind: 'knowledge' });
      fileChanged({ path: 'src/x.ts', tabId: 'A', kind: 'source' });   // mid-run edit → NOT an output
      fileChanged({ path: '.artifacts/run.json', tabId: 'A', kind: 'process' }); // noise → NOT an output
      fileChanged({ path: 'src/y.ts', tabId: 'A', kind: 'source-final' }); // finish batch → output
    });
    await settleCanvas();
    // knowledge + source-final = 2; source + process excluded.
    expect(result.current.outputCount).toBe(2);
  });

  it('ignores a write stamped for a DIFFERENT tab (no cross-tab bleed)', async () => {
    const { result } = renderHook(() =>
      useCanvasHost({ activeTabId: 'A', sessionId: 's-A', isStreaming: false }),
    );
    act(() => {
      fileChanged({ path: 'other.py', tabId: 'B', kind: 'source-final' }); // background tab
    });
    await settleCanvas();
    expect(result.current.outputCount).toBe(0);
  });
});
