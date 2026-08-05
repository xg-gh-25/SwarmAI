/**
 * Tests for the Canvas shell-bar redesign (run_496c3be7).
 *
 * The redesign polishes FileViewerPanel's OWN shell header (Bar 1) so it reads
 * as subordinate to the FileEditorCore file toolbar below it — WITHOUT merging
 * the two (they are separate owners). This test locks the two behavioral changes
 * that are asserined by structure, not pixels:
 *   1. The collapse-to-rail button's icon is `right_panel_close` (a "tuck to the
 *      side" glyph), NOT `close` (which reads as "destroy"). Behavior unchanged.
 *   2. canvasCountTitle() — the pure helper backing the count tooltip.
 *
 * The visual changes (height, accent-wash bg, faint-idle control weight) are
 * pure CSS and intentionally NOT asserted here (that would be brittle
 * test-theater — verified by build + visual instead).
 *
 * FileViewer + CanvasOutputRail are leaf-stubbed — this asserts the PANEL shell.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import FileViewerPanel, { canvasCountTitle } from '../FileViewerPanel';

vi.mock('../FileViewer', () => ({
  default: () => <div data-testid="file-viewer-stub" />,
}));
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

describe('FileViewerPanel — shell-bar redesign', () => {
  it('collapse-to-rail button uses the right_panel_close icon (not close)', () => {
    render(<FileViewerPanel {...baseProps} />);
    const btn = screen.getByTestId('canvas-collapse-rail-btn');
    const icon = btn.querySelector('.material-symbols-outlined');
    expect(icon?.textContent).toBe('right_panel_close');
    // and NOT the old destroy-reading glyph
    expect(icon?.textContent).not.toBe('close');
  });

  it('collapse button keeps its collapse-to-rail label + testid (behavior unchanged)', () => {
    render(<FileViewerPanel {...baseProps} />);
    // aria-label is what existing tests + a11y rely on — must survive the icon swap
    expect(screen.getByLabelText(/collapse canvas to a side rail/i)).toBeTruthy();
  });

  // Gate-2 CRITICAL regression guard: idle and active colors must be MUTUALLY
  // EXCLUSIVE in one slot. If both a dim idle class AND the primary active class
  // are stacked, Tailwind's generated-CSS source order (not className order)
  // decides the winner — the idle color silently overrides the active accent, so
  // a pinned/muted control never shows its active state. These tests assert the
  // ternary contract: exactly ONE color class per state, primary when active.
  it('pin button: active state has the primary accent and NOT the idle dim class', () => {
    render(<FileViewerPanel {...baseProps} pinned />);
    const cls = screen.getByLabelText(/toggle pin/i).className;
    expect(cls).toContain('text-[var(--color-primary)]');
    expect(cls).not.toContain('text-[var(--color-text-dim)]');
  });
  it('pin button: idle state has the dim class and NOT the primary accent', () => {
    render(<FileViewerPanel {...baseProps} pinned={false} />);
    const cls = screen.getByLabelText(/toggle pin/i).className;
    expect(cls).toContain('text-[var(--color-text-dim)]');
    expect(cls).not.toContain('text-[var(--color-primary)]');
  });
  it('mute button: active state has the primary accent and NOT the idle dim class', () => {
    render(<FileViewerPanel {...baseProps} muted />);
    const cls = screen.getByLabelText(/toggle auto-surface mute/i).className;
    expect(cls).toContain('text-[var(--color-primary)]');
    expect(cls).not.toContain('text-[var(--color-text-dim)]');
  });
});

describe('canvasCountTitle — count tooltip helper', () => {
  it('both new and modified', () => {
    expect(canvasCountTitle(2, 1)).toBe('2 new, 1 modified');
  });
  it('only new', () => {
    expect(canvasCountTitle(3, 0)).toBe('3 new');
  });
  it('only modified', () => {
    expect(canvasCountTitle(0, 4)).toBe('4 modified');
  });
  it('neither — empty string (unbadged files, no misleading 0·0)', () => {
    expect(canvasCountTitle(0, 0)).toBe('');
  });
});
