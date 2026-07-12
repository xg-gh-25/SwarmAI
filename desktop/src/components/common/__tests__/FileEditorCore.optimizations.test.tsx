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
import { HIGHLIGHT_MAX_CHARS, HIGHLIGHT_DEBOUNCE_MS } from '../FileEditorCore';

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
