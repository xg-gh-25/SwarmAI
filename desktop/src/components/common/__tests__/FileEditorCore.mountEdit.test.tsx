/**
 * FileEditorCore — an edit made right after the editor appears is kept.
 *
 * Bug: the effect that resets the editor's state when a different file is shown also
 * ran on mount. React runs that effect after the editor is painted, so an edit made in
 * the gap between the paint and the effect (a fast user, or a test on a loaded CI
 * runner) was overwritten with the file's initial text and the tab never turned dirty.
 * The state's initial values already equal what the reset sets, so the editor now
 * resets only when the file it was set up for changes.
 *
 * Same gap, second case: the editor subscribed to file-changed events in a passive
 * effect too, so an agent write announced in that gap was never picked up and the
 * editor kept showing the old text. It subscribes in a layout effect now, which runs
 * before the editor can be seen.
 *
 * The tests render without act(), so React schedules the mount effects as it does in
 * the app, and edits the textarea from a MutationObserver callback: after the commit,
 * before the effects run.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { createRoot, type Root } from 'react-dom/client';

const get = vi.fn(async (url: string) => (url === '/workspace/file' ? { data: { content: 'second version' } } : { data: {} }));
vi.mock('../../../services/api', () => ({ default: { get: (url: string) => get(url), put: vi.fn(), post: vi.fn() } }));
import FileEditorCore, { FILE_CHANGE_COALESCE_MS } from '../FileEditorCore';

const g = globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean };
let prevAct: boolean | undefined;
let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  get.mockClear();
  prevAct = g.IS_REACT_ACT_ENVIRONMENT;
  g.IS_REACT_ACT_ENVIRONMENT = false;
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
});
afterEach(() => {
  root.unmount();
  host.remove();
  g.IS_REACT_ACT_ENVIRONMENT = prevAct;
});

const nextTask = () => new Promise((r) => setTimeout(r, 0));

function typeInto(ta: HTMLTextAreaElement, value: string) {
  Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(ta, value);
  ta.dispatchEvent(new Event('input', { bubbles: true }));
}

describe('an edit right after mount', () => {
  it('survives the mount effects and marks the editor dirty', async () => {
    const onDirtyChange = vi.fn();
    let editedBeforeEffects = false;
    const seen = new Promise<HTMLTextAreaElement>((resolve) => {
      const mo = new MutationObserver(() => {
        const ta = host.querySelector<HTMLTextAreaElement>('[data-testid="file-editor-textarea"]');
        if (!ta) return;
        mo.disconnect();
        // Mount effects have not run yet: the dirty report is one of them.
        editedBeforeEffects = onDirtyChange.mock.calls.length === 0;
        typeInto(ta, 'first version, edited');
        resolve(ta);
      });
      mo.observe(host, { childList: true, subtree: true });
    });
    root.render(
      <FileEditorCore
        filePath="notes.txt" fileName="notes.txt" workspaceId="" initialContent="first version"
        onSave={vi.fn().mockResolvedValue(undefined)} onClose={vi.fn()} variant="panel"
        onDirtyChange={onDirtyChange}
      />,
    );
    const ta = await seen;
    for (let i = 0; i < 5; i += 1) await nextTask();

    expect(editedBeforeEffects).toBe(true);
    expect(ta.value).toBe('first version, edited');
    expect(onDirtyChange).toHaveBeenLastCalledWith(true);
  });

  it('picks up a file change announced before the mount effects run', async () => {
    let announcedBeforeEffects = false;
    const onDirtyChange = vi.fn();
    const seen = new Promise<HTMLTextAreaElement>((resolve) => {
      const mo = new MutationObserver(() => {
        const ta = host.querySelector<HTMLTextAreaElement>('[data-testid="file-editor-textarea"]');
        if (!ta) return;
        mo.disconnect();
        announcedBeforeEffects = onDirtyChange.mock.calls.length === 0;
        window.dispatchEvent(new CustomEvent('swarm:file-changed', { detail: { path: 'notes.txt', operation: 'written' } }));
        resolve(ta);
      });
      mo.observe(host, { childList: true, subtree: true });
    });
    root.render(
      <FileEditorCore
        filePath="notes.txt" fileName="notes.txt" workspaceId="" initialContent="first version"
        onSave={vi.fn().mockResolvedValue(undefined)} onClose={vi.fn()} variant="panel"
        onDirtyChange={onDirtyChange}
      />,
    );
    const ta = await seen;
    await new Promise((r) => setTimeout(r, FILE_CHANGE_COALESCE_MS + 50));
    for (let i = 0; i < 5; i += 1) await nextTask();

    expect(announcedBeforeEffects).toBe(true);
    expect(get).toHaveBeenCalledWith('/workspace/file');
    expect(ta.value).toBe('second version');
  });
});
