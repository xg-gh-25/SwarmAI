/**
 * FileViewerPanel Canvas-content-column layout containment (run_1cb87e1a).
 *
 * ROOT FIX for the recurring "Canvas 开着时 chat input 输入卡死" lag: the chat
 * textarea's auto-grow reflow (a document-wide layout invalidation) was cascading
 * into the Canvas panel's large un-virtualized DOM (FileEditorCore renders one node
 * per line). `contain: layout` on the INNER content column bounds that reflow so the
 * browser treats the subtree as a layout black box — per-keystroke cost stops scaling
 * with Canvas line count.
 *
 * This asserts the containment is present AND on the correct node: the INNER content
 * column (which holds the Canvas outputs navbar + FileViewer), NOT the outer panel box
 * (which holds the overhanging `.canvas-spout` — containment there would clip it).
 *
 * Mutation check: remove `style={{ contain: 'layout' }}` from the content column →
 * `content column has contain:layout` RED. Move it to the outer box → `outer panel box
 * does NOT set contain (spout must not be clipped)` RED.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import FileViewerPanel from '../FileViewerPanel';

vi.mock('../FileViewer', () => ({ default: () => <div data-testid="file-viewer-stub" /> }));
vi.mock('../CanvasOutputRail', () => ({
  CanvasOutputRail: () => <div data-testid="rail-stub" />,
  isBookkeepingPath: () => false,
}));

const baseProps = {
  sessionId: 'sess-1',
  onClose: vi.fn(),
  pinned: false,
  onTogglePin: vi.fn(),
  muted: false,
  onToggleMute: vi.fn(),
};

beforeEach(() => {
  localStorage.clear();
  vi.spyOn(window, 'requestAnimationFrame').mockImplementation((cb: FrameRequestCallback) => { cb(0); return 0 as unknown as number; });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('FileViewerPanel Canvas-content-column containment', () => {
  it('the content column (parent of the outputs navbar + FileViewer) sets contain:layout', () => {
    render(<FileViewerPanel {...baseProps} />);
    // The outputs navbar lives inside the content column; the FileViewer stub is its
    // sibling. Their common parent IS the content column.
    const navbar = screen.getByTestId('canvas-region-outputs');
    const contentColumn = navbar.parentElement as HTMLElement;
    expect(contentColumn).toBeTruthy();
    // Sanity: this really is the content column (holds both the navbar and the viewer).
    expect(contentColumn.querySelector('[data-testid="file-viewer-stub"]')).toBeTruthy();
    expect(contentColumn.style.contain).toBe('layout');
  });

  it('the outer panel box (holding the spout) does NOT set contain (spout must overhang, not be clipped)', () => {
    render(<FileViewerPanel {...baseProps} />);
    const outer = screen.getByTestId('file-viewer-panel') as HTMLElement;
    // The spout is a direct child of the outer box.
    expect(outer.querySelector('[data-testid="canvas-spout"]')).toBeTruthy();
    // Outer box must NOT be layout/paint/size-contained — that would establish a
    // containing block that clips the left:-10px overhanging spout.
    expect(outer.style.contain).toBeFalsy();
  });
});
