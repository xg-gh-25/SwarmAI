/**
 * Tests for FileViewerPanel window/content control redesign (Cycle 3, bug6).
 *
 * The redesign (business rules under test):
 *  - Window states collapse to Panel/Expanded (ONE toggle) + Close. The old
 *    200px COLLAPSED_WIDTH narrow dock is GONE — no [data-testid=
 *    file-viewer-panel-collapsed] can ever render.
 *  - Controls are semantically grouped: pin+mute (content controls) on the LEFT
 *    beside "Outputs"; expand+close (window controls) on the RIGHT; a divider
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

describe('FileViewerPanel — control redesign (bug6)', () => {
  it('renders the full panel (never the removed 200px collapsed dock)', () => {
    render(<FileViewerPanel {...baseProps} />);
    expect(screen.getByTestId('file-viewer-panel')).toBeTruthy();
    // The narrow dock must be structurally impossible now.
    expect(screen.queryByTestId('file-viewer-panel-collapsed')).toBeNull();
  });

  it('exposes exactly the 2-state window model: Expand toggle + Close (no Collapse-to-dock)', () => {
    render(<FileViewerPanel {...baseProps} />);
    expect(screen.getByLabelText(/expand/i)).toBeTruthy();
    expect(screen.getByLabelText(/close canvas/i)).toBeTruthy();
    // The old "collapse to outputs dock" control is gone.
    expect(screen.queryByLabelText(/collapse canvas to outputs dock/i)).toBeNull();
  });

  it('groups content controls (pin+mute) LEFT and window controls (expand+close) RIGHT, divided', () => {
    render(<FileViewerPanel {...baseProps} />);
    const left = screen.getByTestId('canvas-content-controls');
    const right = screen.getByTestId('canvas-window-controls');
    // pin + mute live in the LEFT (content) group
    expect(within(left).getByLabelText(/pin/i)).toBeTruthy();
    expect(within(left).getByLabelText(/mute/i)).toBeTruthy();
    // expand + close live in the RIGHT (window) group
    expect(within(right).getByLabelText(/expand/i)).toBeTruthy();
    expect(within(right).getByLabelText(/close canvas/i)).toBeTruthy();
    // a divider separates the two groups
    expect(screen.getByTestId('canvas-controls-divider')).toBeTruthy();
  });
});
