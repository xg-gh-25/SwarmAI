/**
 * FileViewerPanel two-level collapse (v6 Canvas redesign, run_09431085).
 *
 * (1) caret in the OUTPUTS bar folds ONLY the output list (panel stays full width;
 *     list is mounted-but-hidden so counts stay live). Persists to localStorage
 *     'canvasOutputsCollapsed'.
 * (2) window "Collapse Canvas" rails the WHOLE panel to a thin vertical strip
 *     ([data-testid=canvas-rail]) showing 'Canvas · Outputs'; clicking the strip
 *     expands back. Persists to 'canvasRailed'. This is NOT the removed bug6 dock
 *     (a stunted half-panel) — it's an explicit clickable rail with an expand icon.
 *
 * FileViewer + CanvasOutputRail are leaf-stubbed.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import FileViewerPanel from '../FileViewerPanel';

vi.mock('../FileViewer', () => ({ default: () => <div data-testid="file-viewer-stub" /> }));
vi.mock('../CanvasOutputRail', () => ({
  CanvasOutputRail: () => <div data-testid="rail-stub" />,
}));

const baseProps = {
  // run_26aa6caa: rail scope key is the owning tabId (tabScopeKey), not sessionId.
  tabScopeKey: 'tab-1',
  onClose: vi.fn(),
  pinned: false,
  onTogglePin: vi.fn(),
  muted: false,
  onToggleMute: vi.fn(),
  referencedFiles: { written: [] },
};

beforeEach(() => localStorage.clear());

describe('FileViewerPanel — two-level collapse (v6)', () => {
  it('caret folds only the output list (panel + bar stay), and persists', () => {
    render(<FileViewerPanel {...baseProps} />);
    // List visible initially.
    expect(screen.getByTestId('canvas-outputs-list').className).not.toContain('hidden');
    fireEvent.click(screen.getByTestId('canvas-outputs-caret'));
    // List hidden (mounted-but-hidden), but the bar + panel remain.
    expect(screen.getByTestId('canvas-outputs-list').className).toContain('hidden');
    expect(screen.getByTestId('canvas-region-outputs')).toBeTruthy();
    expect(screen.getByTestId('file-viewer-panel')).toBeTruthy();
    expect(localStorage.getItem('canvasOutputsCollapsed')).toBe('1');
  });

  it('restores the folded-list state from localStorage on mount', () => {
    localStorage.setItem('canvasOutputsCollapsed', '1');
    render(<FileViewerPanel {...baseProps} />);
    expect(screen.getByTestId('canvas-outputs-list').className).toContain('hidden');
  });

  it('Collapse Canvas rails the whole panel to a vertical strip; clicking it expands back', () => {
    render(<FileViewerPanel {...baseProps} />);
    // Full panel: no rail, region-outputs present.
    expect(screen.queryByTestId('canvas-rail')).toBeNull();
    fireEvent.click(screen.getByTestId('canvas-collapse-rail-btn'));
    // Railed: the vertical strip is shown, the full OUTPUTS region is gone.
    expect(screen.getByTestId('canvas-rail')).toBeTruthy();
    expect(screen.queryByTestId('canvas-region-outputs')).toBeNull();
    expect(localStorage.getItem('canvasRailed')).toBe('1');
    // Click the rail → expand back to full panel.
    fireEvent.click(screen.getByTestId('canvas-rail'));
    expect(screen.queryByTestId('canvas-rail')).toBeNull();
    expect(screen.getByTestId('canvas-region-outputs')).toBeTruthy();
    expect(localStorage.getItem('canvasRailed')).toBe('0');
  });

  it('restores the railed state from localStorage on mount', () => {
    localStorage.setItem('canvasRailed', '1');
    render(<FileViewerPanel {...baseProps} />);
    expect(screen.getByTestId('canvas-rail')).toBeTruthy();
  });

  it('keeps the output rail MOUNTED while railed (counts stay live, not frozen)', () => {
    // The rail strip shows counts.total, but CanvasOutputRail is the only source
    // of counts. If it unmounts when railed, a file written while collapsed would
    // not update the strip (self-suppressing-count class, IMPROVEMENT.md:7).
    localStorage.setItem('canvasRailed', '1');
    render(<FileViewerPanel {...baseProps} />);
    expect(screen.getByTestId('canvas-rail')).toBeTruthy();
    // The rail (counts source) is still in the tree, just visually hidden.
    expect(screen.getByTestId('rail-stub')).toBeTruthy();
  });

  it('does NOT re-introduce the bug6 200px collapsed dock', () => {
    render(<FileViewerPanel {...baseProps} />);
    expect(screen.queryByTestId('file-viewer-panel-collapsed')).toBeNull();
  });

  // ── un-rail on new file arrival (run_83bc289d) ──
  // Bug: user rails Canvas to a strip, then clicks a new file → Canvas stayed
  // railed (railed is panel-local + localStorage-persisted, nothing reset it on
  // a new file). A new file arriving IS the "I want to see this" intent → un-rail.
  const FILE_A = { filePath: '/ws/a.md', fileName: 'a.md' };
  const FILE_B = { filePath: '/ws/b.md', fileName: 'b.md' };

  it('un-rails when a NEW file arrives while railed (the reported bug)', () => {
    // Reach `railed` via a MANUAL collapse, NOT localStorage+mount-with-file — the
    // latter setup depended on the run_ca6ae4e7 seed bug (mount-with-file used to
    // stay railed), which is now fixed, so that setup would un-rail on mount and
    // never establish the railed precondition (PIT21: a setup encoding old behavior).
    const { rerender } = render(<FileViewerPanel {...baseProps} initialFile={FILE_A} />);
    fireEvent.click(screen.getByTestId('canvas-collapse-rail-btn'));
    expect(screen.getByTestId('canvas-rail')).toBeTruthy();
    // A DIFFERENT file arrives → Canvas auto-expands.
    rerender(<FileViewerPanel {...baseProps} initialFile={FILE_B} />);
    expect(screen.queryByTestId('canvas-rail')).toBeNull();
    expect(screen.getByTestId('canvas-content-column')).toBeTruthy();
    // localStorage cleared too, so a cold reload isn't stuck railed.
    expect(localStorage.getItem('canvasRailed')).toBe('0');
  });

  it('un-rails on a FRESH mount that ALREADY has a file while railed=1 (the cold-mount seed bug)', () => {
    // run_ca6ae4e7: the panel unmounts when isOpen goes false (canvas.close /
    // nav away) while canvasRailed=1 persists. A later swarm:open-file flips
    // isOpen true → the panel remounts FRESH with initialFile ALREADY set. The
    // un-rail effect must still fire — but with prevFileRef seeded to the current
    // file it saw selectedPath===prev on mount and no-op'd, leaving the just-
    // opened file stranded as a 38px strip (observed live via ui_action open).
    // Seeding prevFileRef=undefined makes undefined→file a real transition → un-rail.
    localStorage.setItem('canvasRailed', '1');
    render(<FileViewerPanel {...baseProps} initialFile={FILE_A} />);
    // Opening a file IS the intent to see it — must reveal, not strip.
    expect(screen.queryByTestId('canvas-rail')).toBeNull();
    expect(screen.getByTestId('canvas-content-column')).toBeTruthy();
    expect(localStorage.getItem('canvasRailed')).toBe('0');
  });

  it('STAYS railed on a fresh mount with NO file (manual-open-no-file preference honored)', () => {
    // The undefined-seed fix must NOT over-fire: mounting railed with no file
    // (a manual rail with no open document) has selectedPath===undefined, so the
    // `selectedPath && ...` guard is falsy → it must NOT auto-un-rail.
    localStorage.setItem('canvasRailed', '1');
    render(<FileViewerPanel {...baseProps} />);
    expect(screen.getByTestId('canvas-rail')).toBeTruthy();
  });

  it('does NOT un-rail on a re-render with the SAME file (no false-trigger)', () => {
    // Reach railed via manual collapse while viewing FILE_A (not the old
    // localStorage+mount-with-file setup — see PIT21 note above).
    const { rerender } = render(<FileViewerPanel {...baseProps} initialFile={FILE_A} />);
    fireEvent.click(screen.getByTestId('canvas-collapse-rail-btn')); // re-rail deliberately
    expect(screen.getByTestId('canvas-rail')).toBeTruthy();
    // A re-render with the SAME file (e.g. parent re-render) must NOT pop it open.
    rerender(<FileViewerPanel {...baseProps} initialFile={FILE_A} />);
    expect(screen.getByTestId('canvas-rail')).toBeTruthy();
  });

  it('un-rails after the user manually rails, then opens a different file', () => {
    const { rerender } = render(<FileViewerPanel {...baseProps} initialFile={FILE_A} />);
    fireEvent.click(screen.getByTestId('canvas-collapse-rail-btn'));
    expect(screen.getByTestId('canvas-rail')).toBeTruthy();
    rerender(<FileViewerPanel {...baseProps} initialFile={FILE_B} />);
    expect(screen.queryByTestId('canvas-rail')).toBeNull();
  });
});
