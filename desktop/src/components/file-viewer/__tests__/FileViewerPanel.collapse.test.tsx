/**
 * FileViewerPanel two-level collapse (v6 Canvas redesign, run_09431085;
 * per-tab lift run_5f5e7675 / Bug 2).
 *
 * (1) caret in the OUTPUTS bar folds ONLY the output list (panel stays full width;
 *     list is mounted-but-hidden so counts stay live).
 * (2) window "Collapse Canvas" rails the WHOLE panel to a thin vertical strip
 *     ([data-testid=canvas-rail]) showing 'Canvas · Outputs'; clicking the strip
 *     expands back. This is NOT the removed bug6 dock (a stunted half-panel) — it's
 *     an explicit clickable rail with an expand icon.
 *
 * Bug 2: railed/outputsCollapsed used to be panel-local useState + GLOBAL localStorage
 * (canvasRailed/canvasOutputsCollapsed), which bled across chat tabs (the panel never
 * remounts on tab switch). They are now PER-TAB, owned by useCanvasHost's CanvasTabState
 * slice and passed in via the `collapse` prop + written via `setCollapse`. So this file
 * asserts the PANEL's controlled behavior: it renders from `collapse` and calls
 * `setCollapse` with the right patch on each toggle. The per-tab ISOLATION + restore
 * (the actual bleed fix) is covered in useCanvasHost.test.ts.
 *
 * FileViewer + CanvasOutputRail are leaf-stubbed.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { useState, useCallback } from 'react';
import FileViewerPanel from '../FileViewerPanel';
import type { CanvasCollapse } from '../../../hooks/useCanvasHost';

vi.mock('../FileViewer', () => ({ default: () => <div data-testid="file-viewer-stub" /> }));
vi.mock('../CanvasOutputRail', () => ({
  CanvasOutputRail: () => <div data-testid="rail-stub" />,
}));

const baseProps = {
  tabScopeKey: 'tab-1',
  onClose: vi.fn(),
  pinned: false,
  onTogglePin: vi.fn(),
  muted: false,
  onToggleMute: vi.fn(),
  referencedFiles: { written: [] },
};

/** Controlled harness — mirrors the ChatPage/useCanvasHost seam: holds the per-tab
 *  collapse state and passes it + a stable setCollapse down, so a toggle actually
 *  re-renders the panel with the new collapse value (like the real slice does). */
function Controlled({
  initial = { railed: false, outputsCollapsed: false },
  initialFile,
  onCollapseChange,
  tabScopeKey = 'tab-1',
}: {
  initial?: CanvasCollapse;
  initialFile?: { filePath: string; fileName: string };
  onCollapseChange?: (c: CanvasCollapse) => void;
  tabScopeKey?: string;
}) {
  const [collapse, setC] = useState<CanvasCollapse>(initial);
  const setCollapse = useCallback((p: Partial<CanvasCollapse>) => {
    setC((prev) => {
      const next = { ...prev, ...p };
      onCollapseChange?.(next);
      return next;
    });
  }, [onCollapseChange]);
  return (
    <FileViewerPanel
      {...baseProps}
      tabScopeKey={tabScopeKey}
      initialFile={initialFile}
      collapse={collapse}
      setCollapse={setCollapse}
    />
  );
}

beforeEach(() => vi.clearAllMocks());

describe('FileViewerPanel — two-level collapse (v6, per-tab props)', () => {
  it('caret folds only the output list (panel + bar stay); calls setCollapse', () => {
    const onCollapseChange = vi.fn();
    render(<Controlled onCollapseChange={onCollapseChange} />);
    // List visible initially.
    expect(screen.getByTestId('canvas-outputs-list').className).not.toContain('hidden');
    fireEvent.click(screen.getByTestId('canvas-outputs-caret'));
    // List hidden (mounted-but-hidden), but the bar + panel remain.
    expect(screen.getByTestId('canvas-outputs-list').className).toContain('hidden');
    expect(screen.getByTestId('canvas-region-outputs')).toBeTruthy();
    expect(screen.getByTestId('file-viewer-panel')).toBeTruthy();
    expect(onCollapseChange).toHaveBeenCalledWith({ railed: false, outputsCollapsed: true });
  });

  it('renders the folded-list state from the collapse prop', () => {
    render(<Controlled initial={{ railed: false, outputsCollapsed: true }} />);
    expect(screen.getByTestId('canvas-outputs-list').className).toContain('hidden');
  });

  it('Collapse Canvas rails the whole panel; clicking the rail expands back; setCollapse called both ways', () => {
    const onCollapseChange = vi.fn();
    render(<Controlled onCollapseChange={onCollapseChange} />);
    // Full panel: no rail, region-outputs present.
    expect(screen.queryByTestId('canvas-rail')).toBeNull();
    fireEvent.click(screen.getByTestId('canvas-collapse-rail-btn'));
    // Railed: the vertical strip is shown, the full OUTPUTS region is gone.
    expect(screen.getByTestId('canvas-rail')).toBeTruthy();
    expect(screen.queryByTestId('canvas-region-outputs')).toBeNull();
    expect(onCollapseChange).toHaveBeenCalledWith({ railed: true, outputsCollapsed: false });
    // Click the rail → expand back to full panel.
    fireEvent.click(screen.getByTestId('canvas-rail'));
    expect(screen.queryByTestId('canvas-rail')).toBeNull();
    expect(screen.getByTestId('canvas-region-outputs')).toBeTruthy();
    expect(onCollapseChange).toHaveBeenLastCalledWith({ railed: false, outputsCollapsed: false });
  });

  it('renders the railed state from the collapse prop on mount', () => {
    render(<Controlled initial={{ railed: true, outputsCollapsed: false }} />);
    expect(screen.getByTestId('canvas-rail')).toBeTruthy();
  });

  it('keeps the output rail MOUNTED while railed (counts stay live, not frozen)', () => {
    // The rail strip shows counts.total, but CanvasOutputRail is the only source
    // of counts. If it unmounts when railed, a file written while collapsed would
    // not update the strip (self-suppressing-count class, IMPROVEMENT.md:7).
    render(<Controlled initial={{ railed: true, outputsCollapsed: false }} />);
    expect(screen.getByTestId('canvas-rail')).toBeTruthy();
    // The rail (counts source) is still in the tree, just visually hidden.
    expect(screen.getByTestId('rail-stub')).toBeTruthy();
  });

  it('does NOT re-introduce the bug6 200px collapsed dock', () => {
    render(<Controlled />);
    expect(screen.queryByTestId('file-viewer-panel-collapsed')).toBeNull();
  });

  // ── Un-rail moved to the WRITE side (Bug 2 root fix, run_5f5e7675) ──
  // The panel NO LONGER un-rails itself. A new file arriving un-rails the owning tab
  // at the single write chokepoint in useCanvasHost (its swarm:open-file handler sets
  // railed:false in the same slice write). This removed a render-timing-fragile panel
  // effect that false-fired on tab switch (adversarial HIGH). So the panel is now purely
  // presentational for collapse: it renders `collapse` and calls `setCollapse` on toggle,
  // and MUST NOT flip railed on its own. The un-rail-on-new-file + tab-switch-preservation
  // behavior is tested in useCanvasHost.openfile.test.ts (the real write chokepoint).
  const FILE_A = { filePath: '/ws/a.md', fileName: 'a.md' };
  const FILE_B = { filePath: '/ws/b.md', fileName: 'b.md' };

  it('does NOT un-rail on its own when a new initialFile prop arrives (write-side owns un-rail now)', () => {
    // The panel must NOT contain the un-rail logic anymore. Rail via the button, then a
    // new initialFile prop arrives — the panel must STAY railed (it does not call
    // setCollapse({railed:false}) itself; only the hook's write chokepoint does).
    const onCollapseChange = vi.fn();
    const { rerender } = render(
      <Controlled initial={{ railed: true, outputsCollapsed: false }} initialFile={FILE_A} onCollapseChange={onCollapseChange} />,
    );
    expect(screen.getByTestId('canvas-rail')).toBeTruthy();
    onCollapseChange.mockClear();
    rerender(<Controlled initial={{ railed: true, outputsCollapsed: false }} initialFile={FILE_B} onCollapseChange={onCollapseChange} />);
    // Panel did NOT self-un-rail on the prop change.
    expect(onCollapseChange).not.toHaveBeenCalled();
    expect(screen.getByTestId('canvas-rail')).toBeTruthy();
  });

  it('renders railed=false from the prop as an expanded panel (write-side already un-railed)', () => {
    // When the hook un-rails at the write, the panel just receives railed:false and
    // renders the full panel — no panel-side logic involved.
    render(<Controlled initial={{ railed: false, outputsCollapsed: false }} initialFile={FILE_A} />);
    expect(screen.queryByTestId('canvas-rail')).toBeNull();
    expect(screen.getByTestId('canvas-content-column')).toBeTruthy();
  });

  it('STAYS railed on mount with railed=true prop (no self-un-rail, with or without a file)', () => {
    render(<Controlled initial={{ railed: true, outputsCollapsed: false }} initialFile={FILE_A} />);
    expect(screen.getByTestId('canvas-rail')).toBeTruthy();
  });
});
