/**
 * Tests for FileViewerPanel window/content control redesign (Cycle 3, bug6;
 * window-model updated run_26aa6caa).
 *
 * The redesign (business rules under test):
 *  - Window states collapse to Panel/Expanded (ONE toggle) + a dismiss button.
 *    The old 200px COLLAPSED_WIDTH narrow dock is GONE — no [data-testid=
 *    file-viewer-panel-collapsed] can ever render.
 *  - run_26aa6caa (XG directive "close 不是关闭 而是 collapse"): the header dismiss
 *    button COLLAPSES the Canvas to the side rail (collapseToRail) — it does NOT
 *    unmount. So the RIGHT group is [expand toggle] + [collapse-to-rail]; there is
 *    no "close/unmount" header button anymore (true unmount survives only via the
 *    intrinsic last-file-tab-closed path inside FileViewer).
 *  - Controls are semantically grouped: pin+mute (content controls) on the LEFT
 *    beside "Outputs"; expand+collapse (window controls) on the RIGHT; a divider
 *    between the two groups.
 *
 * FileViewer + CanvasOutputRail are mocked to leaf stubs — this test asserts the
 * PANEL's control shell only, not file rendering.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import FileViewerPanel from '../FileViewerPanel';

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
};

describe('FileViewerPanel — control redesign (bug6)', () => {
  it('renders the full panel (never the removed 200px collapsed dock)', () => {
    render(<FileViewerPanel {...baseProps} />);
    expect(screen.getByTestId('file-viewer-panel')).toBeTruthy();
    // The narrow dock must be structurally impossible now.
    expect(screen.queryByTestId('file-viewer-panel-collapsed')).toBeNull();
  });

  it('exposes exactly the 2-state window model: Expand toggle + Collapse-to-rail (no unmount/Close button)', () => {
    render(<FileViewerPanel {...baseProps} />);
    expect(screen.getByLabelText(/expand/i)).toBeTruthy();
    // run_26aa6caa: the dismiss button collapses to the rail, it does NOT close/unmount.
    expect(screen.getByLabelText(/collapse canvas to a side rail/i)).toBeTruthy();
    // There is no header button that unmounts the Canvas anymore.
    expect(screen.queryByLabelText(/^close canvas$/i)).toBeNull();
  });

  it('groups content controls (pin+mute) LEFT and window controls (expand+collapse) RIGHT, divided', () => {
    render(<FileViewerPanel {...baseProps} />);
    const left = screen.getByTestId('canvas-content-controls');
    const right = screen.getByTestId('canvas-window-controls');
    // pin + mute live in the LEFT (content) group
    expect(within(left).getByLabelText(/pin/i)).toBeTruthy();
    expect(within(left).getByLabelText(/mute/i)).toBeTruthy();
    // expand + collapse-to-rail live in the RIGHT (window) group
    expect(within(right).getByLabelText(/expand/i)).toBeTruthy();
    expect(within(right).getByLabelText(/collapse canvas to a side rail/i)).toBeTruthy();
    // a divider separates the two groups
    expect(screen.getByTestId('canvas-controls-divider')).toBeTruthy();
  });
});
