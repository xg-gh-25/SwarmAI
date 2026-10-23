/**
 * CopyPathButton — the one copy-path of the file viewer (canvas-file-types.md §2b).
 *
 * - success: the check icon shows, then returns after COPIED_MS; "Copied" is announced;
 * - failure: a warning toast carrying the path;
 * - desktop + relative path: resolved via /workspace/file/meta before copying (the
 *   absolute path is what reaches the clipboard); unresolvable → toast, nothing copied;
 * - Hive / web: copied as given;
 * - the click does not reach the row it sits in;
 * - guard: no file in the viewer calls copyToClipboard directly any more.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { readdirSync, readFileSync, statSync } from 'fs';
import { join, resolve } from 'path';

const copy = vi.fn();
vi.mock('../../../utils/clipboard', () => ({ copyToClipboard: (t: string) => copy(t) }));
const get = vi.fn();
vi.mock('../../../services/api', () => ({ default: { get: (...a: unknown[]) => get(...a) } }));
let desktop = true;
vi.mock('../../../services/tauri', () => ({ isDesktop: () => desktop }));
import { CopyPathButton, COPIED_MS } from '../CopyPathButton';

let toasts: string[];
const onToast = (e: Event) => toasts.push((e as CustomEvent).detail.message);
beforeEach(() => {
  copy.mockReset(); get.mockReset(); desktop = true; toasts = [];
  document.addEventListener('swarm:toast', onToast);
});
afterEach(() => { document.removeEventListener('swarm:toast', onToast); vi.useRealTimers(); });

const click = async () => { await act(async () => { fireEvent.click(screen.getByTestId('copy-path-btn')); }); };

describe('CopyPathButton', () => {
  it('copies an absolute path and shows a check for COPIED_MS', async () => {
    vi.useFakeTimers();
    copy.mockResolvedValue(true);
    render(<CopyPathButton path="/ws/a.md" />);
    await click();
    expect(copy).toHaveBeenCalledWith('/ws/a.md');
    const btn = screen.getByTestId('copy-path-btn');
    expect(btn.textContent).toContain('check');
    expect(btn.getAttribute('aria-label')).toBe('Copied');
    act(() => { vi.advanceTimersByTime(COPIED_MS); });
    expect(btn.textContent).toContain('content_copy');
  });

  it('a failed copy raises a toast with the path', async () => {
    copy.mockResolvedValue(false);
    render(<CopyPathButton path="/ws/a.md" />);
    await click();
    expect(toasts).toEqual(['Could not copy — /ws/a.md']);
    expect(screen.getByTestId('copy-path-btn').textContent).toContain('content_copy');
  });

  it('desktop: a relative path is resolved to its absolute path first', async () => {
    copy.mockResolvedValue(true);
    get.mockResolvedValue({ data: { absolute_path: '/Users/x/SwarmWS/docs/a.md' } });
    render(<CopyPathButton path="docs/a.md" />);
    await click();
    expect(get).toHaveBeenCalledWith('/workspace/file/meta', { params: { path: 'docs/a.md' } });
    expect(copy).toHaveBeenCalledWith('/Users/x/SwarmWS/docs/a.md');
  });

  it('desktop: an unresolvable relative path copies nothing and says so', async () => {
    get.mockRejectedValue(new Error('404'));
    render(<CopyPathButton path="gone.md" />);
    await click();
    expect(copy).not.toHaveBeenCalled();
    expect(toasts[0]).toContain('gone.md');
  });

  it('Hive / web: copied as given (the backend path is remote)', async () => {
    desktop = false;
    copy.mockResolvedValue(true);
    render(<CopyPathButton path="docs/a.md" />);
    await click();
    expect(get).not.toHaveBeenCalled();
    expect(copy).toHaveBeenCalledWith('docs/a.md');
  });

  it('the click does not reach the row', async () => {
    copy.mockResolvedValue(true);
    const row = vi.fn();
    render(<div onClick={row}><CopyPathButton path="/ws/a.md" /></div>);
    await click();
    expect(row).not.toHaveBeenCalled();
  });
});

describe('one implementation', () => {
  it('no viewer file calls copyToClipboard directly', () => {
    const root = resolve(__dirname, '../..');
    const files: string[] = [];
    const walk = (d: string) => {
      for (const n of readdirSync(d)) {
        const p = join(d, n);
        if (statSync(p).isDirectory()) { if (n !== '__tests__') walk(p); }
        else if (/\.tsx?$/.test(n) && !/\.test\./.test(n)) files.push(p);
      }
    };
    walk(join(root, 'file-viewer'));
    files.push(join(root, 'common/FileEditorCore.tsx'));
    const offenders = files.filter((f) => /copyToClipboard/.test(readFileSync(f, 'utf8')));
    expect(offenders).toEqual([]);
  });
});
