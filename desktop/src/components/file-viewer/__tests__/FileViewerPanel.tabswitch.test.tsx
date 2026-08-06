/**
 * FileViewerPanel cross-tab bleed repro (run_a9806ea0).
 *
 * The BUG: open a file in Canvas on chat-tab A, switch to chat-tab B → the Canvas
 * still shows tab A's file instead of clearing to B's own (empty) Canvas.
 *
 * Why the existing tests miss it: the containment/controls/etc. panel tests
 * `vi.mock('../FileViewer')` — they STUB the very component whose initialFile /
 * tabScopeKey restore effects carry the bleed. This test renders the REAL FileViewer
 * through a wrapper that mirrors the ChatPage seam:
 *   {isOpen && <FileViewerPanel initialFile={canvasFile} tabScopeKey={activeTabId}/>}
 * driving a genuine chat-tab switch, so the leak on the render path is exercised.
 *
 * Mocks ONLY the leaves: `api` (file content fetch) + FileEditorCore (the heavy
 * editor). The tab-strip renders `tab.fileName` (FileViewerTabBar) — that visible
 * label is the bleed assertion handle.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, act } from '@testing-library/react';
import { useState } from 'react';
import FileViewerPanel from '../FileViewerPanel';

// Leaf editor → a stub that just prints the file it was handed, so we can assert
// which file the Canvas is actually showing.
vi.mock('../../common/FileEditorCore', () => ({
  default: ({ fileName }: { fileName: string }) => <div data-testid="editor-core">{fileName}</div>,
}));

// api.get → resolve path (echo) + file content/meta (minimal text payload).
vi.mock('../../services/api', () => ({
  default: {
    get: vi.fn(async (url: string, opts?: { params?: { path?: string } }) => {
      if (url.includes('/workspace/file/resolve')) {
        return { data: { resolved_path: opts?.params?.path ?? '' } };
      }
      // content + meta fetches → a tiny text file
      return { data: { content: 'hello', encoding: 'utf-8', size: 5, mime_type: 'text/plain', readonly: false } };
    }),
  },
}));

/** Mirror of the ChatPage seam: per-tab canvas file + activeTabId, panel gated on isOpen. */
function Harness() {
  const [activeTabId, setActiveTabId] = useState('tab-A');
  // per-tab canvas file (like useCanvasHost's slice.file, restored on switch)
  const [fileByTab, setFileByTab] = useState<Record<string, { filePath: string; fileName: string } | null>>({
    'tab-A': null, 'tab-B': null,
  });
  const canvasFile = fileByTab[activeTabId];
  const isOpen = !!canvasFile;
  return (
    <div>
      <button data-testid="open-A" onClick={() => setFileByTab((m) => ({ ...m, 'tab-A': { filePath: 'a/alpha.md', fileName: 'alpha.md' } }))}>openA</button>
      <button data-testid="to-B" onClick={() => setActiveTabId('tab-B')}>toB</button>
      <button data-testid="to-A" onClick={() => setActiveTabId('tab-A')}>toA</button>
      {isOpen && (
        <FileViewerPanel
          tabScopeKey={activeTabId}
          initialFile={canvasFile ?? undefined}
          onClose={() => setFileByTab((m) => ({ ...m, [activeTabId]: null }))}
          pinned={false}
          onTogglePin={vi.fn()}
          muted={false}
          onToggleMute={vi.fn()}
          referencedFiles={{ written: [] }}
          collapse={{ railed: false, outputsCollapsed: false }}
          setCollapse={() => {}}
        />
      )}
    </div>
  );
}

beforeEach(() => {
  sessionStorage.clear();
  vi.spyOn(window, 'requestAnimationFrame').mockImplementation((cb: FrameRequestCallback) => { cb(0); return 0 as unknown as number; });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('Canvas cross-tab bleed (real FileViewer render path)', () => {
  it('opening a file on tab A then switching to tab B does NOT leave A\'s file showing', async () => {
    render(<Harness />);
    // open alpha.md on tab A
    await act(async () => { screen.getByTestId('open-A').click(); });
    await screen.findByText('alpha.md');   // tab strip shows it → Canvas open on A

    // switch to tab B (B has no canvas file → panel should be gone / not showing alpha)
    await act(async () => { screen.getByTestId('to-B').click(); });

    // THE BLEED: alpha.md must NOT be visible on tab B
    expect(screen.queryByText('alpha.md')).toBeNull();
  });

  it('BOTH tabs have a file: switching A→B shows ONLY B\'s file, never A\'s (the real bleed)', async () => {
    render(<HarnessBoth />);
    await act(async () => { screen.getByTestId('open-A').click(); });
    await screen.findByText('alpha.md');
    await act(async () => { screen.getByTestId('open-B-and-switch').click(); });
    // On tab B: beta.md shows, alpha.md must be GONE (not lingering as a stale tab)
    await screen.findByText('beta.md');
    expect(screen.queryByText('alpha.md')).toBeNull();
  });
});

/** Both tabs carry a canvas file — the panel stays MOUNTED across the switch, so the
 *  tabScopeKey-change restore path (FileViewer.tsx:194) is what must clear A's tab. */
function HarnessBoth() {
  const [activeTabId, setActiveTabId] = useState('tab-A');
  const [fileByTab, setFileByTab] = useState<Record<string, { filePath: string; fileName: string } | null>>({
    'tab-A': null, 'tab-B': null,
  });
  const canvasFile = fileByTab[activeTabId];
  const isOpen = !!canvasFile;
  return (
    <div>
      <button data-testid="open-A" onClick={() => setFileByTab((m) => ({ ...m, 'tab-A': { filePath: 'a/alpha.md', fileName: 'alpha.md' } }))}>openA</button>
      <button data-testid="open-B-and-switch" onClick={() => { setFileByTab((m) => ({ ...m, 'tab-B': { filePath: 'b/beta.md', fileName: 'beta.md' } })); setActiveTabId('tab-B'); }}>openB+switch</button>
      {isOpen && (
        <FileViewerPanel
          tabScopeKey={activeTabId}
          initialFile={canvasFile ?? undefined}
          onClose={() => setFileByTab((m) => ({ ...m, [activeTabId]: null }))}
          pinned={false}
          onTogglePin={vi.fn()}
          muted={false}
          onToggleMute={vi.fn()}
          referencedFiles={{ written: [] }}
          collapse={{ railed: false, outputsCollapsed: false }}
          setCollapse={() => {}}
        />
      )}
    </div>
  );
}
