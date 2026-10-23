/**
 * FileEditorCore — panel vs modal chrome (Super Canvas commit 3,
 * docs/design/super-canvas.md).
 *
 * Panel (the Canvas): no header and no footer. The controls FLOAT over the bottom
 * of the content (absolutely positioned, not a strip in the layout), and every
 * content scroll layer gets PANEL_FLOAT_PAD bottom padding so the last lines scroll
 * clear of them. The floating group — a single Preview / Source / Changes view switch, Review, Attach — and a
 * Save button that exists only while there are unsaved edits. No Reload, Copy path,
 * mode toggle or Close. Modal: the labelled header and footer are unchanged and
 * there is no panel strip.
 *
 * Drives the real component; only the workspace-root fetch is stubbed.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import FileEditorCore, { PANEL_FLOAT_PAD } from '../FileEditorCore';

vi.mock('../../../services/api', () => ({
  default: { get: vi.fn().mockResolvedValue({ data: {} }), put: vi.fn(), post: vi.fn() },
}));

const base = {
  workspaceId: '',
  onSave: vi.fn().mockResolvedValue(undefined),
  onClose: vi.fn(),
  onAttachToChat: vi.fn(),
};
const md = { ...base, filePath: 'docs/a.md', fileName: 'a.md', initialContent: '# A\n\nbody\n' };
const ts = { ...base, filePath: 'src/x.ts', fileName: 'x.ts', initialContent: 'const x = 1;\n' };

beforeEach(() => cleanup());

const checked = () => screen.getAllByRole('radio').find((r) => r.getAttribute('aria-checked') === 'true')?.textContent;

describe('panel chrome', () => {
  it('has no header or footer controls, only the bottom strip', () => {
    render(<FileEditorCore {...md} variant="panel" committedContent="# old\n" />);
    expect(screen.getByTestId('editor-panel-bar')).toBeTruthy();
    for (const id of ['show-changes-toggle', 'markdown-preview-toggle', 'copy-path-btn', 'mode-toggle', 'file-editor-cancel']) {
      expect(screen.queryByTestId(id)).toBeNull();
    }
    expect(screen.queryByLabelText('Close')).toBeNull();
    expect(screen.getByTestId('review-mode-toggle')).toBeTruthy();
    expect(screen.getByTestId('attach-to-chat-btn')).toBeTruthy();
    // Refresh is in the floating group (super-canvas-fixing.md C2).
    expect(screen.getByTestId('editor-panel-bar').contains(screen.getByTestId('file-editor-reload'))).toBe(true);
  });

  it('Refresh with unsaved edits asks first (Discard & Reload), never silently overwrites', () => {
    render(<FileEditorCore {...ts} variant="panel" />);
    const ta = screen.getByTestId('file-editor-textarea') as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: 'edited' } });
    fireEvent.click(screen.getByTestId('file-editor-reload'));
    expect(screen.getByTestId('unsaved-warning-discard').textContent).toMatch(/Discard & Reload/i);
    expect(ta.value).toBe('edited');
  });

  it('a markdown file switches Preview / Source / Changes with one control', () => {
    render(<FileEditorCore {...md} variant="panel" committedContent="# old\n" />);
    expect(screen.getAllByRole('radio').map((r) => r.textContent)).toEqual(['Preview', 'Source', 'Changes']);
    expect(checked()).toBe('Preview');
    fireEvent.click(screen.getByTestId('editor-view-source'));
    expect(checked()).toBe('Source');
    expect(screen.getByTestId('file-editor-textarea')).toBeTruthy();
    fireEvent.click(screen.getByTestId('editor-view-changes'));
    expect(checked()).toBe('Changes');
    expect(screen.getByTestId('diff-view')).toBeTruthy();
    fireEvent.click(screen.getByTestId('editor-view-preview'));
    expect(checked()).toBe('Preview');
    expect(screen.queryByTestId('diff-view')).toBeNull();
  });

  it('a code file has Source and Changes only; Changes is off with nothing to compare', () => {
    render(<FileEditorCore {...ts} variant="panel" />);
    expect(screen.getAllByRole('radio').map((r) => r.textContent)).toEqual(['Source', 'Changes']);
    expect((screen.getByTestId('editor-view-changes') as HTMLButtonElement).disabled).toBe(true);
  });

  it('Save appears only once there are unsaved edits, and saves', () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<FileEditorCore {...ts} onSave={onSave} variant="panel" />);
    expect(screen.queryByTestId('file-editor-save')).toBeNull();
    fireEvent.change(screen.getByTestId('file-editor-textarea'), { target: { value: 'const x = 2;\n' } });
    fireEvent.click(screen.getByTestId('file-editor-save'));
    expect(onSave).toHaveBeenCalledWith('const x = 2;\n');
  });
});

describe('the bar floats over the content', () => {
  const pad = (el: Element) => (el as HTMLElement).style.paddingBottom;

  it('is positioned over the content, not laid out below it', () => {
    render(<FileEditorCore {...ts} variant="panel" />);
    const bar = screen.getByTestId('editor-panel-bar');
    expect(bar.className).toMatch(/\babsolute\b/);
    expect(bar.className).toContain('bottom-0');
    expect(bar.className).toContain('pointer-events-none'); // only its buttons take clicks
    expect(bar.className).toContain('editor-float-ink');
    expect(bar.querySelector('.editor-float-group')).toBeTruthy();
    // the selected view is the paper chip, the others are not
    expect(screen.getByTestId('editor-view-source').className).toContain('editor-view-on');
    expect(screen.getByTestId('editor-view-changes').className).not.toContain('editor-view-on');
  });

  it('source view: textarea and highlight layer share the same end padding', () => {
    render(<FileEditorCore {...ts} variant="panel" />);
    expect(pad(screen.getByTestId('file-editor-textarea'))).toBe(`${PANEL_FLOAT_PAD}px`);
    expect(pad(screen.getByTestId('editor-highlight-layer'))).toBe(`${PANEL_FLOAT_PAD}px`);
  });

  it('preview and diff views get it too; diff shows the comment hint in the bar', () => {
    const { container } = render(<FileEditorCore {...md} variant="panel" committedContent="# old\n" />);
    const preview = container.querySelector('.canvas-doc')!.parentElement!;
    expect(pad(preview)).toBe(`${PANEL_FLOAT_PAD}px`);
    fireEvent.click(screen.getByTestId('editor-view-changes'));
    expect(pad(screen.getByTestId('diff-view'))).toBe(`${PANEL_FLOAT_PAD}px`);
    expect(screen.getByTestId('diff-comment-hint')).toBeTruthy();
  });

  it('the modal adds no end padding', () => {
    render(<FileEditorCore {...ts} variant="modal" />);
    expect(pad(screen.getByTestId('file-editor-textarea'))).toBe('');
  });
});

describe('modal chrome (unchanged)', () => {
  it('keeps the labelled header toggles and footer, and has no panel strip', () => {
    render(<FileEditorCore {...ts} variant="modal" committedContent="const x = 0;\n" />);
    expect(screen.getByTestId('show-changes-toggle').textContent).toContain('Show Changes');
    expect(screen.getByTestId('file-editor-save')).toBeTruthy();
    expect(screen.queryByTestId('editor-panel-bar')).toBeNull();
  });
});
