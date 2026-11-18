/**
 * FileEditorCore — review comments are a batch, sent to the chat as ONE message.
 *
 * - B1: Add on a selection keeps the comment pending (nothing is sent); the tray
 *   shows the count; its Send hands every comment, with the text it was made on, to
 *   the chat in one message (autoSend + append, so a typed draft is kept) and empties
 *   the list.
 * - B2: a comment on a selection in the markdown preview is quoted (no line map),
 *   and its place stays painted as pending until the comment leaves the list.
 * - B3: Send now sends only that comment, at once, and it never joins the batch.
 * - B4: the tray list edits and removes a comment.
 * - B5: a gutter comment sent on its own leaves the batch, so the batch Send cannot
 *   send it a second time; a selection comment opened from the gutter is sent with
 *   its own quote.
 * - B6: when nothing receives the message (no chat input, e.g. a read-only tab) the
 *   comments are kept; a draft kept by the chat still empties the list (the comments
 *   are in the chat box now).
 * - B7: Clear all asks once before clearing.
 * - B8: a pending preview mark is dropped once its range no longer covers the quote,
 *   also when only the content changed.
 * - B9: the outcome is said in a toast: no chat, a kept draft, and sent from the
 *   full-screen editor (the chat is behind it); a sent message in the panel needs none.
 * - B10: the selection comment box follows the selection when the source view scrolls.
 *
 * The real component; api stubbed; jsdom selection APIs driven directly.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, within } from '@testing-library/react';
import type { ComponentProps } from 'react';
import FileEditorCore from '../FileEditorCore';
import { ToastContext } from '../../../contexts/ToastContext';
import { INJECT_CHAT_INPUT, type InjectChatInputDetail } from '../../../pages/chat/injectChatInput';

vi.mock('../../../services/api', () => ({
  default: { get: vi.fn().mockResolvedValue({ data: {} }), put: vi.fn(), post: vi.fn() },
}));

const base = { workspaceId: '', onSave: vi.fn().mockResolvedValue(undefined), onClose: vi.fn(), variant: 'panel' as const };
const md = { ...base, filePath: 'docs/b.md', fileName: 'b.md', initialContent: '# Title\n\nalpha beta\ngamma\n\n## Two\n\ndelta\n' };

let injected: InjectChatInputDetail[];
// Stands in for the chat input: records the event and reports it as sent (or as `reply`).
let reply: InjectChatInputDetail['outcome'] = 'sent';
const onInject = (e: Event) => {
  const d = (e as CustomEvent<InjectChatInputDetail>).detail;
  injected.push({ ...d });
  d.outcome = reply;
};
beforeEach(() => { injected = []; reply = 'sent'; sessionStorage.clear(); window.addEventListener(INJECT_CHAT_INPUT, onInject); });
afterEach(() => { window.removeEventListener(INJECT_CHAT_INPUT, onInject); cleanup(); window.getSelection()?.removeAllRanges(); vi.unstubAllGlobals(); });

function selectInTextarea(start: number, end: number) {
  const ta = screen.getByTestId('file-editor-textarea') as HTMLTextAreaElement;
  ta.setSelectionRange(start, end);
  fireEvent.select(ta);
}

function comment(text: string, action: 'add' | 'send' = 'add') {
  fireEvent.click(screen.getByTestId('selection-comment-btn'));
  fireEvent.change(screen.getByTestId('selection-comment-input'), { target: { value: text } });
  fireEvent.click(screen.getByTestId(action === 'add' ? 'selection-comment-add' : 'selection-comment-send'));
}

function selectInPreview(match: string): Range {
  const preview = screen.getByTestId('markdown-preview-box');
  const p = [...preview.querySelectorAll('p')].find((el) => el.textContent?.includes(match))!;
  const range = document.createRange();
  range.selectNodeContents(p);
  range.getBoundingClientRect = () => ({ top: 100, bottom: 120, left: 40, right: 200, width: 160, height: 20, x: 40, y: 100, toJSON: () => ({}) });
  window.getSelection()!.removeAllRanges();
  window.getSelection()!.addRange(range);
  fireEvent.mouseUp(preview);
  return range;
}

const content = md.initialContent;
const at = (s: string) => content.indexOf(s);

describe('B1 — comments wait, then go as one message', () => {
  it('three Adds send nothing; the tray Send sends all three, quoted, in line order, once', () => {
    render(<FileEditorCore {...md} />);
    fireEvent.click(screen.getByTestId('editor-view-source'));
    selectInTextarea(at('delta'), at('delta') + 5);
    comment('make it bold');
    selectInTextarea(at('alpha beta'), at('alpha beta') + 10);
    comment('reword');
    selectInTextarea(at('gamma'), at('gamma') + 5);
    comment('drop this');

    expect(injected).toHaveLength(0);
    expect(screen.getByTestId('review-tray-count').textContent).toContain('3');

    fireEvent.click(screen.getByTestId('review-tray-send'));
    expect(injected).toHaveLength(1);
    const msg = injected[0];
    expect(msg).toMatchObject({ autoSend: true, append: true });
    expect(msg.text).toContain('`docs/b.md`');
    const order = ['reword', 'drop this', 'make it bold'].map((t) => msg.text.indexOf(t));
    expect(order.every((i) => i >= 0)).toBe(true);
    expect([...order].sort((a, b) => a - b)).toEqual(order); // line order: 3, 4, 8
    expect(msg.text).toContain('Line 3] reword\n   > alpha beta');
    expect(msg.text).toContain('§Two, Line 8] make it bold\n   > delta');

    expect(screen.queryByTestId('review-tray')).toBeNull();
    expect(sessionStorage.getItem('swarm:review-comments:docs/b.md')).toBeNull();
  });
});

describe('B2 — a comment in the markdown preview', () => {
  it('is quoted without a line, and its place is painted as pending until it leaves the list', () => {
    const set = vi.fn();
    const del = vi.fn();
    class FakeHighlight { ranges: Range[]; constructor(...r: Range[]) { this.ranges = r; } }
    vi.stubGlobal('CSS', { highlights: { set, delete: del } });
    vi.stubGlobal('Highlight', FakeHighlight);
    render(<FileEditorCore {...md} />);
    selectInPreview('alpha');
    comment('tighten');

    expect(injected).toHaveLength(0);
    const pending = set.mock.calls.filter(([name]) => name === 'swarm-comment-pending');
    expect(pending.length).toBeGreaterThan(0);
    const hl = pending[pending.length - 1][1] as FakeHighlight;
    expect(hl.ranges).toHaveLength(1);
    expect(hl.ranges[0].toString()).toContain('alpha beta');

    fireEvent.click(screen.getByTestId('review-tray-count'));
    fireEvent.click(within(screen.getByTestId('review-tray-list')).getByTestId('review-tray-remove'));
    expect(del).toHaveBeenCalledWith('swarm-comment-pending');
    expect(screen.queryByTestId('review-tray')).toBeNull();
  });

  it('the batch message quotes the preview selection', () => {
    render(<FileEditorCore {...md} />);
    selectInPreview('delta');
    comment('expand');
    fireEvent.click(screen.getByTestId('review-tray-send'));
    expect(injected).toHaveLength(1);
    expect(injected[0].text).toContain('[preview selection] expand\n   > delta');
  });
});

describe('B3 — Send now', () => {
  it('sends only this comment, at once, and it never joins the batch', () => {
    render(<FileEditorCore {...md} />);
    fireEvent.click(screen.getByTestId('editor-view-source'));
    selectInTextarea(at('gamma'), at('gamma') + 5);
    comment('now please', 'send');
    expect(injected).toHaveLength(1);
    expect(injected[0]).toMatchObject({ autoSend: true, append: true });
    expect(injected[0].text).toContain('now please');
    expect(screen.queryByTestId('review-tray')).toBeNull();
  });
});

describe('B4 — the tray list', () => {
  it('edits and removes comments', () => {
    render(<FileEditorCore {...md} />);
    fireEvent.click(screen.getByTestId('editor-view-source'));
    selectInTextarea(at('alpha beta'), at('alpha beta') + 10);
    comment('first');
    selectInTextarea(at('delta'), at('delta') + 5);
    comment('second');

    fireEvent.click(screen.getByTestId('review-tray-count'));
    const list = screen.getByTestId('review-tray-list');
    const items = within(list).getAllByTestId('review-tray-item');
    expect(items).toHaveLength(2);
    fireEvent.click(within(items[0]).getByTestId('review-tray-edit'));
    fireEvent.change(within(items[0]).getByTestId('review-tray-edit-input'), { target: { value: 'first, edited' } });
    fireEvent.click(within(items[0]).getByTestId('review-tray-edit-save'));
    fireEvent.click(within(within(screen.getByTestId('review-tray-list')).getAllByTestId('review-tray-item')[1]).getByTestId('review-tray-remove'));

    fireEvent.click(screen.getByTestId('review-tray-send'));
    expect(injected).toHaveLength(1);
    expect(injected[0].text).toContain('first, edited');
    expect(injected[0].text).not.toContain('second');
  });
});

describe('B5 — a gutter comment sent on its own leaves the batch', () => {
  it('Add on a line, then Send now on it: sent once, gone from the tray', () => {
    render(<FileEditorCore {...md} />);
    fireEvent.click(screen.getByTestId('review-mode-toggle'));
    const line = (n: number) => within(screen.getByTestId('review-gutter-line-numbers')).getAllByRole('button')[n - 1];
    fireEvent.click(line(3));
    expect(screen.getByTestId('comment-box-quote').textContent).toBe('alpha beta');
    fireEvent.change(screen.getByTestId('comment-input'), { target: { value: 'on line 3' } });
    fireEvent.click(screen.getByTestId('comment-submit'));
    expect(screen.getByTestId('review-tray-count').textContent).toContain('1');

    fireEvent.click(line(3));
    fireEvent.click(screen.getByTestId('comment-send-single'));
    expect(injected).toHaveLength(1);
    expect(injected[0].text).toContain('on line 3');
    expect(screen.queryByTestId('review-tray')).toBeNull();
  });
});

describe('B5b — a selection comment opened from the gutter', () => {
  it('Send now sends it with its own multi-line quote and removes that comment', () => {
    render(<FileEditorCore {...md} />);
    fireEvent.click(screen.getByTestId('editor-view-source'));
    selectInTextarea(at('beta'), at('gamma') + 3); // "beta\ngam": part of lines 3-4
    comment('two lines');
    fireEvent.click(screen.getByTestId('review-mode-toggle'));
    const line = (n: number) => within(screen.getByTestId('review-gutter-line-numbers')).getAllByRole('button')[n - 1];
    fireEvent.click(line(4));
    expect((screen.getByTestId('comment-input') as HTMLTextAreaElement).value).toBe('two lines');
    fireEvent.click(screen.getByTestId('comment-send-single'));
    expect(injected).toHaveLength(1);
    expect(injected[0].text).toContain('```\nbeta\ngam\n```'); // its quote, not the whole lines
    expect(screen.queryByTestId('review-tray')).toBeNull();
  });
});

describe('B6 — what the chat did decides what the list keeps', () => {
  it('nothing listening: the batch Send keeps every comment', () => {
    render(<FileEditorCore {...md} />);
    fireEvent.click(screen.getByTestId('editor-view-source'));
    selectInTextarea(at('gamma'), at('gamma') + 5);
    comment('keep me');
    reply = undefined; // no chat input answered
    fireEvent.click(screen.getByTestId('review-tray-send'));
    expect(injected).toHaveLength(1);
    expect(screen.getByTestId('review-tray-count').textContent).toContain('1');
  });

  it('nothing listening: Send now keeps the comment in the list instead of losing it', () => {
    reply = undefined;
    render(<FileEditorCore {...md} />);
    fireEvent.click(screen.getByTestId('editor-view-source'));
    selectInTextarea(at('gamma'), at('gamma') + 5);
    comment('not lost', 'send');
    expect(screen.getByTestId('review-tray-count').textContent).toContain('1');
  });

  it('the chat kept a draft (appended, not sent): the list empties, the comments are in the chat box', () => {
    render(<FileEditorCore {...md} />);
    fireEvent.click(screen.getByTestId('editor-view-source'));
    selectInTextarea(at('gamma'), at('gamma') + 5);
    comment('into the draft');
    reply = 'appended';
    fireEvent.click(screen.getByTestId('review-tray-send'));
    expect(screen.queryByTestId('review-tray')).toBeNull();
  });
});

describe('B7 — Clear all asks once', () => {
  it('the first click asks, the second clears', () => {
    render(<FileEditorCore {...md} />);
    fireEvent.click(screen.getByTestId('editor-view-source'));
    selectInTextarea(at('gamma'), at('gamma') + 5);
    comment('one');
    fireEvent.click(screen.getByTestId('review-tray-count'));
    fireEvent.click(screen.getByTestId('review-tray-clear'));
    expect(screen.getByTestId('review-tray-count').textContent).toContain('1');
    fireEvent.click(screen.getByTestId('review-tray-clear'));
    expect(screen.queryByTestId('review-tray')).toBeNull();
    expect(injected).toHaveLength(0);
  });
});

describe('B8 — a pending preview mark that no longer covers its quote', () => {
  it('is dropped', () => {
    const set = vi.fn();
    class FakeHighlight { ranges: Range[]; constructor(...r: Range[]) { this.ranges = r; } }
    vi.stubGlobal('CSS', { highlights: { set, delete: vi.fn() } });
    vi.stubGlobal('Highlight', FakeHighlight);
    render(<FileEditorCore {...md} />);
    selectInPreview('alpha');
    comment('first');
    // The preview's text changed in place under the first range.
    const preview = screen.getByTestId('markdown-preview-box');
    const p = [...preview.querySelectorAll('p')].find((el) => el.textContent?.includes('alpha'))!;
    (p.firstChild as Text).data = 'something else';
    selectInPreview('delta');
    comment('second');
    const last = set.mock.calls.filter(([n]) => n === 'swarm-comment-pending').pop()![1] as FakeHighlight;
    expect(last.ranges.map((r) => r.toString())).toEqual(['delta']);
  });
});

describe('B6b — gutter Send now with nothing listening', () => {
  it('keeps an existing comment with the edited text, and adds a new one to the batch', () => {
    reply = undefined;
    render(<FileEditorCore {...md} />);
    fireEvent.click(screen.getByTestId('review-mode-toggle'));
    const line = (n: number) => within(screen.getByTestId('review-gutter-line-numbers')).getAllByRole('button')[n - 1];
    fireEvent.click(line(3));
    fireEvent.change(screen.getByTestId('comment-input'), { target: { value: 'new one' } });
    fireEvent.click(screen.getByTestId('comment-send-single'));
    expect(screen.getByTestId('review-tray-count').textContent).toContain('1');
    fireEvent.click(line(3));
    fireEvent.change(screen.getByTestId('comment-input'), { target: { value: 'edited' } });
    fireEvent.click(screen.getByTestId('comment-send-single'));
    expect(screen.getByTestId('review-tray-count').textContent).toContain('1');
    fireEvent.click(screen.getByTestId('review-tray-count'));
    expect(within(screen.getByTestId('review-tray-list')).getByText('edited')).toBeTruthy();
  });
});

describe('B8b — only the content changed', () => {
  it('a pending preview mark whose text is gone is dropped without any new comment', () => {
    const set = vi.fn();
    const del = vi.fn();
    class FakeHighlight { ranges: Range[]; constructor(...r: Range[]) { this.ranges = r; } }
    vi.stubGlobal('CSS', { highlights: { set, delete: del } });
    vi.stubGlobal('Highlight', FakeHighlight);
    const { rerender } = render(<FileEditorCore {...md} />);
    selectInPreview('alpha');
    comment('first');
    set.mockClear();
    del.mockClear();
    rerender(<FileEditorCore {...md} initialContent={md.initialContent.replace('alpha beta', 'omega')} />);
    const lastSet = set.mock.calls.filter(([n]) => n === 'swarm-comment-pending').pop();
    const dropped = del.mock.calls.some(([n]) => n === 'swarm-comment-pending')
      || (lastSet && (lastSet[1] as FakeHighlight).ranges.length === 0);
    expect(dropped).toBe(true);
  });
});

describe('B9 — the outcome is said', () => {
  function renderWithToasts(props: Partial<ComponentProps<typeof FileEditorCore>> = {}) {
    const addToast = vi.fn(() => 'id');
    render(
      <ToastContext.Provider value={{ addToast, removeToast: vi.fn(), toasts: [] }}>
        <FileEditorCore {...md} {...props} />
      </ToastContext.Provider>,
    );
    return addToast;
  }
  function addAndSend() {
    const edit = screen.queryByTestId('editor-view-source'); // the panel opens .md in preview
    if (edit) fireEvent.click(edit);
    selectInTextarea(at('gamma'), at('gamma') + 5);
    comment('x');
    fireEvent.click(screen.getByTestId('review-tray-send'));
  }
  it('no chat: a warning', () => {
    reply = undefined;
    const addToast = renderWithToasts();
    addAndSend();
    expect(addToast).toHaveBeenCalledWith(expect.objectContaining({ severity: 'warning' }));
  });
  it('a kept draft: an info toast', () => {
    reply = 'appended';
    const addToast = renderWithToasts();
    addAndSend();
    expect(addToast).toHaveBeenCalledWith(expect.objectContaining({ severity: 'info' }));
  });
  it('sent from the panel: no toast; from the full-screen editor: a success toast', () => {
    const panel = renderWithToasts();
    addAndSend();
    expect(panel).not.toHaveBeenCalled();
    cleanup();
    const modal = renderWithToasts({ variant: 'modal', filePath: 'docs/b.txt', fileName: 'b.txt' });
    addAndSend();
    expect(modal).toHaveBeenCalledWith(expect.objectContaining({ severity: 'success' }));
  });
});

describe('B10 — the selection comment box follows a source-view scroll', () => {
  it('moves by the distance the textarea scrolled', () => {
    render(<FileEditorCore {...md} />);
    fireEvent.click(screen.getByTestId('editor-view-source'));
    selectInTextarea(at('gamma'), at('gamma') + 5);
    fireEvent.click(screen.getByTestId('selection-comment-btn'));
    const box = () => parseFloat((screen.getByTestId('selection-comment-popover') as HTMLElement).style.top);
    const before = box();
    const ta = screen.getByTestId('file-editor-textarea') as HTMLTextAreaElement;
    ta.scrollTop = 30;
    fireEvent.scroll(ta);
    expect(box()).toBeCloseTo(before - 30, 3);
  });
});
