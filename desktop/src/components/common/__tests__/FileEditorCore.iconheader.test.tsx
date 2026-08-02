/**
 * AC2 / AC6 — variant-gated header: panel = icon-only (label in title tooltip),
 * modal = icon+text (unchanged). Regression guard for the Canvas redesign.
 *
 * The panel Canvas needs a compact icon-only file-operation header; the fullscreen
 * modal keeps its roomy text-labeled header. This test asserts BOTH: a stateful
 * toggle ("Show Changes") shows NO text in panel mode (state survives via icon +
 * active-tint + title) but DOES show text in modal mode.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import FileEditorCore from '../FileEditorCore';

// Stub the workspace-root fetch so mount doesn't hit the network.
vi.mock('../../../services/api', () => ({
  default: { get: vi.fn().mockResolvedValue({ data: {} }), put: vi.fn(), post: vi.fn() },
}));

const baseProps = {
  filePath: 'src/pages/index.tsx',
  fileName: 'index.tsx',
  workspaceId: '',
  initialContent: 'const x = 1;\nconst y = 2;\n',
  committedContent: 'const x = 0;\n', // makes it dirty → Show Changes enabled
  onSave: vi.fn().mockResolvedValue(undefined),
  onClose: vi.fn(),
} as const;

beforeEach(() => cleanup());

describe('FileEditorCore header — variant-gated icon-only (AC2/AC6)', () => {
  it('PANEL variant: Show Changes button shows NO text (icon-only), label in title', () => {
    render(<FileEditorCore {...baseProps} variant="panel" />);
    const btn = screen.getByTestId('show-changes-toggle');
    // Icon-only: the visible text "Show Changes" must be absent.
    expect(btn.textContent).not.toContain('Show Changes');
    // The label is preserved as a hover tooltip.
    expect(btn.getAttribute('title')).toMatch(/show changes/i);
    // The material icon is still present.
    expect(btn.querySelector('.material-symbols-outlined')).toBeTruthy();
  });

  it('MODAL variant: Show Changes button KEEPS its text label (unchanged UX)', () => {
    render(<FileEditorCore {...baseProps} variant="modal" />);
    const btn = screen.getByTestId('show-changes-toggle');
    expect(btn.textContent).toContain('Show Changes');
  });

  it('PANEL variant WITH onToggleMode: window-group divider present (separates file-actions from mode+close)', () => {
    render(<FileEditorCore {...baseProps} variant="panel" onToggleMode={vi.fn()} />);
    expect(screen.getByTestId('editor-header-window-divider')).toBeTruthy();
  });

  it('PANEL variant WITHOUT onToggleMode: NO window divider (Close alone → no dangling divider)', () => {
    // The Canvas panel does not pass onToggleMode; the only window control is
    // Close, so a divider would dangle. Adversarial MED (run_f44a17f5).
    render(<FileEditorCore {...baseProps} variant="panel" />);
    expect(screen.queryByTestId('editor-header-window-divider')).toBeNull();
  });

  it('MODAL variant: no icon-only window divider (text header, no regrouping)', () => {
    render(<FileEditorCore {...baseProps} variant="modal" onToggleMode={vi.fn()} />);
    expect(screen.queryByTestId('editor-header-window-divider')).toBeNull();
  });
});
