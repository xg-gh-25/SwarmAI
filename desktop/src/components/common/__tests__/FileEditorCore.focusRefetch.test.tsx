/**
 * FileEditorCore — refetch on window focus (super-canvas-fixing.md C2, F1).
 *
 * Clicking back into SwarmAI from an editor beside it fires `focus`, never
 * `visibilitychange` (the window was not hidden). The editor's existing refetch
 * must run on both, with the same rules: at most once per 3 s since the last fetch,
 * never while there are unsaved edits, and only applied if the disk differs.
 *
 * Time is faked to step past the 3 s throttle; api.get is the boundary.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, act, fireEvent } from '@testing-library/react';

const get = vi.fn();
vi.mock('../../../services/api', () => ({ default: { get: (...a: unknown[]) => get(...a), put: vi.fn(), post: vi.fn() } }));
import FileEditorCore from '../FileEditorCore';

const props = {
  filePath: 'src/x.ts', fileName: 'x.ts', workspaceId: '', initialContent: 'old\n',
  onSave: vi.fn().mockResolvedValue(undefined), onClose: vi.fn(), variant: 'panel' as const,
};
const fileReads = () => get.mock.calls.filter((c) => c[0] === '/workspace/file').length;

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] });
  get.mockReset();
  get.mockImplementation(async (url: string) => (url === '/workspace/file' ? { data: { content: 'new from disk\n' } } : { data: {} }));
});
afterEach(() => vi.useRealTimers());

async function focusWindow() {
  await act(async () => { window.dispatchEvent(new Event('focus')); await Promise.resolve(); await Promise.resolve(); });
}

describe('refetch on window focus', () => {
  it('reads the file again and shows the disk content', async () => {
    render(<FileEditorCore {...props} />);
    vi.setSystemTime(Date.now() + 4000);
    await focusWindow();
    expect(fileReads()).toBe(1);
    expect((screen.getByTestId('file-editor-textarea') as HTMLTextAreaElement).value).toBe('new from disk\n');
  });

  it('is throttled to once per 3 s', async () => {
    render(<FileEditorCore {...props} />);
    await focusWindow(); // right after mount: inside the 3 s window
    expect(fileReads()).toBe(0);
  });

  it('never touches unsaved edits', async () => {
    render(<FileEditorCore {...props} />);
    fireEvent.change(screen.getByTestId('file-editor-textarea'), { target: { value: 'mine\n' } });
    vi.setSystemTime(Date.now() + 4000);
    await focusWindow();
    expect(fileReads()).toBe(0);
    expect((screen.getByTestId('file-editor-textarea') as HTMLTextAreaElement).value).toBe('mine\n');
  });
});
