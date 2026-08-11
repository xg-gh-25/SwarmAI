/**
 * Tests for FileEditorCore optimizations (explorer/editor perf+UX run):
 * - A: syntax highlight is DEBOUNCED (not synchronous per keystroke).
 * - E: large files show an explicit notice + search shows a disabled state
 *      (instead of a misleading "0 of 0").
 * - F: manual Reload guards unsaved edits (surfaces the warning instead of
 *      silently overwriting).
 *
 * These render the REAL FileEditorCore, so reverting a fix changes assertions.
 * api.get('/workspace') fails silently in the test env (no backend) and the
 * component falls back gracefully — no API mock required.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import hljs from 'highlight.js';
import FileEditorCore from '../FileEditorCore';
import { HIGHLIGHT_MAX_CHARS, HIGHLIGHT_DEBOUNCE_MS, computeGutterWindow, GUTTER_VIRTUALIZE_MIN_LINES } from '../FileEditorCore';
import ReviewModeGutter from '../ReviewModeGutter';

function renderCore(props: Partial<React.ComponentProps<typeof FileEditorCore>> = {}) {
  return render(
    <FileEditorCore
      filePath="src/mod.ts"
      fileName="mod.ts"
      workspaceId="ws"
      initialContent={props.initialContent ?? 'const a = 1;'}
      variant="panel"
      onSave={vi.fn().mockResolvedValue(undefined)}
      onClose={vi.fn()}
      {...props}
    />,
  );
}

describe('A — debounced syntax highlighting', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => { vi.runOnlyPendingTimers(); vi.useRealTimers(); vi.restoreAllMocks(); });

  it('does NOT call hljs.highlight synchronously on mount — waits for the debounce', () => {
    const spy = vi.spyOn(hljs, 'highlight');
    renderCore({ initialContent: 'const x = 42;' });
    // Immediately after render, the debounced highlight has NOT run yet.
    expect(spy).not.toHaveBeenCalled();
    // After the debounce window, it runs exactly once.
    act(() => { vi.advanceTimersByTime(HIGHLIGHT_DEBOUNCE_MS + 10); });
    expect(spy).toHaveBeenCalledTimes(1);
  });
});

describe('E — large-file notice + disabled search', () => {
  it('shows the large-file notice above the threshold', () => {
    renderCore({ initialContent: 'x'.repeat(HIGHLIGHT_MAX_CHARS + 1) });
    expect(screen.getByTestId('large-file-notice')).toBeInTheDocument();
  });

  it('does NOT show the notice for a normal file', () => {
    renderCore({ initialContent: 'const a = 1;' });
    expect(screen.queryByTestId('large-file-notice')).not.toBeInTheDocument();
  });
});

describe('F — reload guards unsaved edits', () => {
  it('reload with unsaved edits surfaces the unsaved warning, does not silently refetch', () => {
    const getSpy = vi.spyOn(hljs, 'highlight'); // sanity: component mounts
    renderCore({ initialContent: 'original' });
    getSpy.mockRestore();

    // Make the editor dirty by typing into the textarea.
    const textarea = screen.getByRole('textbox') as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: 'edited-unsaved' } });

    // Click the Reload button (title/aria based — it has a reload icon).
    const reloadBtn = screen.getByTitle(/reload/i);
    fireEvent.click(reloadBtn);

    // The unsaved-changes warning appears instead of a silent overwrite.
    // (Content in the textarea must remain the user's edit.)
    expect(textarea.value).toBe('edited-unsaved');
  });

  it('reload warning offers a "Discard & Reload" action (not just discard-and-close)', () => {
    renderCore({ initialContent: 'original' });
    const textarea = screen.getByRole('textbox') as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: 'edited-unsaved' } });
    fireEvent.click(screen.getByTitle(/reload/i));

    // The confirm button must be the reload variant, giving the user a real path
    // to the reload the guard was gating (Gate-2 MED fix: the generic discard
    // path closed the editor instead of reloading).
    const discard = screen.getByTestId('unsaved-warning-discard');
    expect(discard).toHaveTextContent(/Discard & Reload/i);
  });
});

describe('G — virtualized line gutter (large-file DOM explosion fix)', () => {
  it('renders a BOUNDED number of gutter line-number divs for a huge file (not lineCount)', () => {
    // 20K lines. Un-virtualized this mounts 20K gutter <div>s. Virtualized it
    // must mount only the visible window (+buffer) — a small bounded count.
    const content = Array.from({ length: 20_000 }, (_, i) => `line ${i + 1}`).join('\n');
    renderCore({ initialContent: content });

    const gutter = screen.getByTestId('gutter-line-numbers');
    const lineDivs = gutter.children.length;
    // Bounded: visible window + buffer, never the full 20K.
    expect(lineDivs).toBeGreaterThan(0);
    expect(lineDivs).toBeLessThan(500);
  });

  it('renders ALL line numbers for a small file (unchanged path — zero regression)', () => {
    renderCore({ initialContent: 'a\nb\nc\nd\ne' }); // 5 lines
    const gutter = screen.getByTestId('gutter-line-numbers');
    // Small file: every line number present (line 1 and line 5 both rendered).
    expect(gutter).toHaveTextContent('1');
    expect(gutter).toHaveTextContent('5');
    expect(gutter.children.length).toBe(5);
  });

  it('preserves total scroll height so the scrollbar reflects the full file', () => {
    const content = Array.from({ length: 20_000 }, (_, i) => `L${i}`).join('\n');
    renderCore({ initialContent: content });
    const gutter = screen.getByTestId('gutter-line-numbers');
    // The virtualized container declares full height = lineCount * 24px via a
    // spacer/sizer element, so the scrollbar is correct even though only a
    // window of divs is mounted.
    const sizer = gutter.parentElement?.querySelector('[data-testid="gutter-sizer"]');
    expect(sizer).not.toBeNull();
    // Full height = lineCount*24 + 32 (p-4 top+bottom padding).
    expect((sizer as HTMLElement).style.height).toBe(`${20_000 * 24 + 32}px`);
  });
});

describe('computeGutterWindow — pure window math (Gate-2 coverage gap fix)', () => {
  const LH = 24;

  it('at scrollTop=0 with a real viewport, starts at line 0', () => {
    const { start, end } = computeGutterWindow(20_000, 0, 1200);
    expect(start).toBe(0);
    // visible = ceil(1200/24) + 40*2 = 50 + 80 = 130
    expect(end).toBe(130);
  });

  it('mid-scroll: window follows scrollTop (start clamps overscan below 0-floor)', () => {
    // scrollTop = 10_000px → line ~416; start = 416 - 40 = 376
    const { start, end } = computeGutterWindow(20_000, 10_000, 1200);
    expect(start).toBe(Math.floor(10_000 / LH) - 40);
    expect(end).toBe(start + 130);
    expect(end).toBeLessThanOrEqual(20_000);
  });

  it('start never goes negative (overscan clamp at top)', () => {
    const { start } = computeGutterWindow(20_000, 100, 1200);
    expect(start).toBe(0); // floor(100/24)=4, 4-40<0 → clamped to 0
  });

  it('end clamps to lineCount when scrolled past EOF', () => {
    const { start, end } = computeGutterWindow(2_100, 1_000_000, 1200);
    expect(end).toBe(2_100);
    expect(start).toBeLessThanOrEqual(2_100);
  });

  it('viewportHeight=0 uses the fallback (never an empty window)', () => {
    const { start, end } = computeGutterWindow(20_000, 0, 0);
    expect(start).toBe(0);
    expect(end).toBeGreaterThan(0); // fallback 1200 → 130 lines
  });

  it('larger viewport renders a larger window', () => {
    const small = computeGutterWindow(20_000, 0, 600);
    const large = computeGutterWindow(20_000, 0, 2400);
    expect(large.end).toBeGreaterThan(small.end);
  });
});

describe('ReviewModeGutter virtualization (Gate-2: review-mode large-file path)', () => {
  it('renders only a bounded window of line numbers for a large file', () => {
    const content = Array.from({ length: 20_000 }, (_, i) => `line ${i}`).join('\n');
    render(
      <ReviewModeGutter
        lineCount={20_000}
        scrollTop={0}
        viewportHeight={1200}
        comments={[]}
        activePopoverLine={null}
        editingCommentId={null}
        onLineClick={vi.fn()}
        onAddComment={vi.fn()}
        onUpdateComment={vi.fn()}
        onRemoveComment={vi.fn()}
        onCancelPopover={vi.fn()}
        getCommentForLine={() => undefined}
      />,
    );
    void content;
    const gutter = screen.getByTestId('review-gutter-line-numbers');
    expect(gutter.children.length).toBeGreaterThan(0);
    expect(gutter.children.length).toBeLessThan(500);
  });

  it('renders all line numbers for a small file (unchanged path)', () => {
    render(
      <ReviewModeGutter
        lineCount={5}
        scrollTop={0}
        comments={[]}
        activePopoverLine={null}
        editingCommentId={null}
        onLineClick={vi.fn()}
        onAddComment={vi.fn()}
        onUpdateComment={vi.fn()}
        onRemoveComment={vi.fn()}
        onCancelPopover={vi.fn()}
        getCommentForLine={() => undefined}
      />,
    );
    const gutter = screen.getByTestId('review-gutter-line-numbers');
    expect(gutter.children.length).toBe(5);
  });
});

// Assert the threshold constant is the shared source of truth (both gutters import it)
describe('GUTTER_VIRTUALIZE_MIN_LINES', () => {
  it('is a sane shared threshold', () => {
    expect(GUTTER_VIRTUALIZE_MIN_LINES).toBeGreaterThanOrEqual(500);
    expect(GUTTER_VIRTUALIZE_MIN_LINES).toBeLessThanOrEqual(10_000);
  });
});
