/**
 * FileEditorCore — editing, review and selection in the Canvas panel
 * (super-canvas-fixing.md round 2, G1-G4).
 *
 * - G1: the editable view's chip reads "Edit" (Preview / Edit / Changes).
 * - G2: turning Review on from the markdown preview switches to the editable view,
 *   where the review gutter is.
 * - G3: a selection — in the source textarea or in the markdown preview — shows ONE
 *   selection toolbar, rendered into document.body (never inside the editor, whose
 *   Canvas ancestor is a containing block for fixed elements); Comment opens the
 *   comment popover.
 * - G5: while the selection-comment popover is open, the selected text stays marked
 *   (source view: an overlay <mark>; preview: a CSS Custom Highlight), and the mark
 *   goes away when the popover closes.
 * - G4: Attach to chat dispatches swarm:inject-chat-input with append + focus, no
 *   autoSend, the file path, the line range (source view) and the text quoted.
 *
 * The real component; api stubbed; jsdom selection APIs driven directly.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import FileEditorCore from '../FileEditorCore';
import { INJECT_CHAT_INPUT, type InjectChatInputDetail } from '../../../pages/chat/injectChatInput';

vi.mock('../../../services/api', () => ({
  default: { get: vi.fn().mockResolvedValue({ data: {} }), put: vi.fn(), post: vi.fn() },
}));

const base = { workspaceId: '', onSave: vi.fn().mockResolvedValue(undefined), onClose: vi.fn(), variant: 'panel' as const };
const md = { ...base, filePath: 'docs/a.md', fileName: 'a.md', initialContent: '# Title\n\nalpha beta\ngamma\n' };

let injected: InjectChatInputDetail[];
const onInject = (e: Event) => injected.push((e as CustomEvent<InjectChatInputDetail>).detail);
beforeEach(() => { injected = []; window.addEventListener(INJECT_CHAT_INPUT, onInject); });
afterEach(() => { window.removeEventListener(INJECT_CHAT_INPUT, onInject); cleanup(); window.getSelection()?.removeAllRanges(); });

function selectInTextarea(start: number, end: number) {
  const ta = screen.getByTestId('file-editor-textarea') as HTMLTextAreaElement;
  ta.setSelectionRange(start, end);
  fireEvent.select(ta);
}

describe('G1 — the editable view is called Edit', () => {
  it('Preview / Edit / Changes', () => {
    render(<FileEditorCore {...md} />);
    expect(screen.getAllByRole('radio').map((r) => r.textContent)).toEqual(['Preview', 'Edit', 'Changes']);
  });
});

describe('G2 — Review from the preview', () => {
  it('switches to the editable view with the review gutter', () => {
    render(<FileEditorCore {...md} />);
    expect(screen.queryByTestId('file-editor-textarea')).toBeNull(); // md opens in preview
    fireEvent.click(screen.getByTestId('review-mode-toggle'));
    expect(screen.getByTestId('file-editor-textarea')).toBeTruthy();
    expect(screen.getByTestId('review-gutter-line-numbers')).toBeTruthy();
  });
});

describe('G3/G4 — selection toolbar', () => {
  it('a source-view selection shows the toolbar in document.body, outside the editor', () => {
    const { container } = render(<FileEditorCore {...md} />);
    fireEvent.click(screen.getByTestId('editor-view-source'));
    selectInTextarea(9, 19); // "alpha beta"
    const bar = screen.getByTestId('selection-toolbar');
    expect(bar.parentElement).toBe(document.body);
    expect(container.contains(bar)).toBe(false);
    fireEvent.click(screen.getByTestId('selection-comment-btn'));
    expect(screen.getByTestId('selection-comment-popover')).toBeTruthy();
  });

  it('Attach to chat appends a quote with path and line range, focused, not sent', () => {
    render(<FileEditorCore {...md} />);
    fireEvent.click(screen.getByTestId('editor-view-source'));
    selectInTextarea(9, 25); // "alpha beta\ngamma" → lines 3-4
    fireEvent.click(screen.getByTestId('selection-attach-btn'));
    expect(injected).toHaveLength(1);
    expect(injected[0]).toMatchObject({ append: true, focus: true });
    expect(injected[0].autoSend).toBeFalsy();
    expect(injected[0].text).toBe('From `docs/a.md` lines 3-4:\n> alpha beta\n> gamma\n');
    expect(screen.queryByTestId('selection-toolbar')).toBeNull();
  });

  it('a selection in the markdown preview shows the same toolbar; attach has no line range', () => {
    render(<FileEditorCore {...md} />);
    const preview = screen.getByTestId('markdown-preview-box');
    const p = [...preview.querySelectorAll('p')].find((el) => el.textContent?.includes('alpha'))!;
    const range = document.createRange();
    range.selectNodeContents(p);
    // jsdom has no layout: give the range a rect like a browser would
    range.getBoundingClientRect = () => ({ top: 100, bottom: 120, left: 40, right: 200, width: 160, height: 20, x: 40, y: 100, toJSON: () => ({}) });
    window.getSelection()!.removeAllRanges();
    window.getSelection()!.addRange(range);
    fireEvent.mouseUp(preview);
    const bar = screen.getByTestId('selection-toolbar');
    expect(bar.parentElement).toBe(document.body);
    expect(bar.style.top).toBe('124px'); // under the selection
    fireEvent.click(screen.getByTestId('selection-attach-btn'));
    expect(injected[0].text).toMatch(/^From `docs\/a\.md`:\n> alpha beta/);
  });

  it('a collapsed selection in the preview shows nothing', () => {
    render(<FileEditorCore {...md} />);
    window.getSelection()!.removeAllRanges();
    fireEvent.mouseUp(screen.getByTestId('markdown-preview-box'));
    expect(screen.queryByTestId('selection-toolbar')).toBeNull();
  });
});

describe('G5 — the commented selection stays marked while the popover is open', () => {
  it('source view: the selected text is marked until the popover is dismissed', () => {
    render(<FileEditorCore {...md} />);
    fireEvent.click(screen.getByTestId('editor-view-source'));
    selectInTextarea(9, 19); // "alpha beta"
    expect(screen.queryByTestId('comment-selection-mark')).toBeNull(); // only once Comment is clicked
    fireEvent.click(screen.getByTestId('selection-comment-btn'));
    const mark = screen.getByTestId('comment-selection-mark');
    expect(mark.querySelector('mark')?.textContent).toBe('alpha beta');
    expect(mark.querySelector('span')?.textContent).toBe('# Title\n\n'); // layout prefix
    fireEvent.mouseDown(document.body); // click outside closes the popover
    expect(screen.queryByTestId('selection-comment-popover')).toBeNull();
    expect(screen.queryByTestId('comment-selection-mark')).toBeNull();
  });

  it('source view: sending the comment clears the mark', () => {
    render(<FileEditorCore {...md} />);
    fireEvent.click(screen.getByTestId('editor-view-source'));
    selectInTextarea(9, 19);
    fireEvent.click(screen.getByTestId('selection-comment-btn'));
    fireEvent.change(screen.getByTestId('selection-comment-input'), { target: { value: 'reword' } });
    fireEvent.click(screen.getByTestId('selection-comment-send'));
    expect(injected[0].text).toContain('alpha beta');
    expect(screen.queryByTestId('comment-selection-mark')).toBeNull();
  });

  it('preview: the selected range is registered as a CSS highlight and removed on close', () => {
    const set = vi.fn();
    const del = vi.fn();
    class FakeHighlight { ranges: Range[]; constructor(...r: Range[]) { this.ranges = r; } }
    vi.stubGlobal('CSS', { highlights: { set, delete: del } });
    vi.stubGlobal('Highlight', FakeHighlight);
    try {
      render(<FileEditorCore {...md} />);
      const preview = screen.getByTestId('markdown-preview-box');
      const p = [...preview.querySelectorAll('p')].find((el) => el.textContent?.includes('alpha'))!;
      const range = document.createRange();
      range.selectNodeContents(p);
      range.getBoundingClientRect = () => ({ top: 100, bottom: 120, left: 40, right: 200, width: 160, height: 20, x: 40, y: 100, toJSON: () => ({}) });
      window.getSelection()!.removeAllRanges();
      window.getSelection()!.addRange(range);
      fireEvent.mouseUp(preview);
      expect(set).not.toHaveBeenCalled();
      fireEvent.click(screen.getByTestId('selection-comment-btn'));
      expect(set).toHaveBeenCalledTimes(1);
      const [name, hl] = set.mock.calls[0] as [string, FakeHighlight];
      expect(name).toBe('swarm-comment-selection');
      expect(hl.ranges[0].toString()).toContain('alpha beta');
      fireEvent.mouseDown(document.body); // click outside closes the popover
      expect(del).toHaveBeenCalledWith('swarm-comment-selection');
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
