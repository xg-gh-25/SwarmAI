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

  // ── un-rail on new file arrival (run_83bc289d) ──
  // Bug: user rails Canvas to a strip, then a new file arrives → Canvas must un-rail
  // (a new file IS the "I want to see this" intent). Now that railed is per-tab, this
  // must fire ONLY on a same-tab new-file transition, NOT on a tab switch (which also
  // changes the file but must preserve the incoming tab's restored railed state).
  const FILE_A = { filePath: '/ws/a.md', fileName: 'a.md' };
  const FILE_B = { filePath: '/ws/b.md', fileName: 'b.md' };

  it('un-rails when a NEW file arrives while railed (same tab — the reported bug)', () => {
    // One Controlled instance (same tabScopeKey), internal collapse state. Rail via the
    // button (state→railed:true), then a DIFFERENT file arrives via a new initialFile
    // prop → the un-rail effect fires (same tab, path changed) → setCollapse({railed:false}).
    const { rerender } = render(
      <Controlled initial={{ railed: false, outputsCollapsed: false }} initialFile={FILE_A} />,
    );
    fireEvent.click(screen.getByTestId('canvas-collapse-rail-btn'));
    expect(screen.getByTestId('canvas-rail')).toBeTruthy();
    // Same tab, new file → auto-expand (initialFile IS a live prop, so this changes it).
    rerender(<Controlled initial={{ railed: false, outputsCollapsed: false }} initialFile={FILE_B} />);
    expect(screen.queryByTestId('canvas-rail')).toBeNull();
  });

  it('un-rails on a fresh mount that ALREADY has a file while railed=1 (cold-mount seed bug)', () => {
    // run_ca6ae4e7: a remount with initialFile set + railed must still reveal the file
    // (prevFileRef/prevRailTabRef seeded so undefined→file on the SAME tab is a real
    // transition). The un-rail calls setCollapse({railed:false}).
    const onCollapseChange = vi.fn();
    render(<Controlled initial={{ railed: true, outputsCollapsed: false }} initialFile={FILE_A} onCollapseChange={onCollapseChange} />);
    // Opening a file IS the intent to see it — must reveal, not strip.
    expect(screen.queryByTestId('canvas-rail')).toBeNull();
    expect(screen.getByTestId('canvas-content-column')).toBeTruthy();
    expect(onCollapseChange).toHaveBeenCalledWith({ railed: false, outputsCollapsed: false });
  });

  it('STAYS railed on a fresh mount with NO file (manual-open-no-file preference honored)', () => {
    // The undefined-seed fix must NOT over-fire: mounting railed with no file has
    // selectedPath===undefined, so the `selectedPath && ...` guard is falsy.
    render(<Controlled initial={{ railed: true, outputsCollapsed: false }} />);
    expect(screen.getByTestId('canvas-rail')).toBeTruthy();
  });

  it('does NOT un-rail on a TAB SWITCH that restores a railed tab (Bug 2 — per-tab railed preserved)', () => {
    // The key Bug-2 regression guard: a tab switch changes BOTH tabScopeKey AND the
    // restored file, but the incoming tab's railed state must survive (NOT be popped
    // open by the un-rail effect — Gate-1 finding). Harness mirrors useCanvasHost:
    // a per-tab collapse map, switching activeTab restores that tab's slice.
    const perTab: Record<string, CanvasCollapse> = {
      'tab-A': { railed: false, outputsCollapsed: false },
      'tab-B': { railed: true, outputsCollapsed: false }, // tab B was left railed
    };
    const perTabFile: Record<string, { filePath: string; fileName: string }> = {
      'tab-A': FILE_A,
      'tab-B': FILE_B,
    };
    function SwitchHarness({ activeTab }: { activeTab: string }) {
      return (
        <FileViewerPanel
          {...baseProps}
          tabScopeKey={activeTab}
          initialFile={perTabFile[activeTab]}
          collapse={perTab[activeTab]}
          setCollapse={(p) => { perTab[activeTab] = { ...perTab[activeTab], ...p }; }}
        />
      );
    }
    const { rerender } = render(<SwitchHarness activeTab="tab-A" />);
    expect(screen.queryByTestId('canvas-rail')).toBeNull();
    // Switch to tab-B: tabScopeKey changes AND the restored collapse is railed=true.
    // The un-rail effect must see tabSwitched=true and NOT un-rail → stays railed.
    rerender(<SwitchHarness activeTab="tab-B" />);
    expect(screen.getByTestId('canvas-rail')).toBeTruthy();
    // And tab B's railed state was NOT written to false by an un-rail.
    expect(perTab['tab-B'].railed).toBe(true);
  });
});
