/**
 * MarkdownRenderer — `#base=<ref>` fragment on file links / inline-code paths (E3).
 *
 * Drives the REAL MarkdownRenderer (markdown → react-markdown → `a` / InlineCode
 * handlers → OPEN_FILE_EVENT on document) and captures the dispatched detail.
 *
 * Invariants:
 * - Any `#...` fragment is stripped before path detection; the dispatched `path`
 *   never carries it (and the link is still treated as a workspace file).
 * - `#base=<sha>[^]` with a 7-40 hex sha (URL-decoded, `%5E` → `^`) adds
 *   `baseRef` + `autoDiff:false` — the CanvasOutputRail.outputRowOpenDetail shape.
 * - Invalid refs (`../x`, `HEAD`, `;rm`) and non-base fragments → plain `{ path }`.
 * - Links without a fragment and external URLs behave exactly as before.
 * - The path is URL-decoded (spaces / non-ASCII open); a malformed escape keeps the
 *   raw path; `%23` stays in the path; decoded `..` is still rejected.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, fireEvent } from '@testing-library/react';

vi.mock('../../../services/tauri', () => ({ getApiBaseUrl: () => 'http://localhost:18321' }));
vi.mock('mermaid', () => ({ default: { initialize: vi.fn(), render: vi.fn() } }));
vi.mock('highlight.js', () => ({ default: { highlightElement: vi.fn(), configure: vi.fn() } }));
vi.mock('../../../contexts/ThemeContext', () => ({ useTheme: () => ({ resolvedTheme: 'light' }) }));
vi.mock('../../../utils/clipboard', () => ({ copyToClipboard: vi.fn() }));
vi.mock('../../../utils/openExternal', () => ({ openExternal: vi.fn() }));

import MarkdownRenderer, {
  OPEN_FILE_EVENT,
  parseOpenFileTarget,
  buildOpenFileDetail,
  isWorkspaceFilePath,
} from '../MarkdownRenderer';
import { outputRowOpenDetail } from '../../file-viewer/CanvasOutputRail';
import { openExternal } from '../../../utils/openExternal';

const ABS = '/Users/me/repo/backend/core/x.py';
const SHA = 'abc1234';

let details: Array<Record<string, unknown>>;
const onOpen = (e: Event) => details.push((e as CustomEvent).detail);

beforeEach(() => {
  details = [];
  vi.mocked(openExternal).mockClear();
  document.addEventListener(OPEN_FILE_EVENT, onOpen);
});
afterEach(() => {
  document.removeEventListener(OPEN_FILE_EVENT, onOpen);
});

/** Render markdown, click the first <a>, return the dispatched details. */
function clickLink(md: string) {
  const { container } = render(<MarkdownRenderer content={md} />);
  const a = container.querySelector('a');
  expect(a).not.toBeNull();
  fireEvent.click(a!);
  return { a: a!, details };
}

/** Render markdown, click the first <code>, return the dispatched details. */
function clickCode(md: string) {
  const { container } = render(<MarkdownRenderer content={md} />);
  const code = container.querySelector('code');
  expect(code).not.toBeNull();
  fireEvent.click(code!);
  return { code: code!, details };
}

describe('markdown link with #base=<ref>', () => {
  it('valid #base=sha^ → path without fragment + baseRef + rail autoDiff flag', () => {
    const { a } = clickLink(`[backend/core/x.py](${ABS}#base=${SHA}^)`);
    expect(details).toEqual([{ path: ABS, autoDiff: false, baseRef: `${SHA}^` }]);
    // Same shape the OUTPUTS rail builds for a source-final row.
    expect(details[0]).toEqual(outputRowOpenDetail(ABS, 'upd', ABS, `${SHA}^`));
    // Still rendered as a workspace link (href kept), not an external link.
    expect(a.getAttribute('href')).not.toBeNull();
    expect(a.getAttribute('role')).toBeNull();
    expect(openExternal).not.toHaveBeenCalled();
  });

  it('decodes %5E to ^ (explicit encoded href)', () => {
    clickLink(`[x](${ABS}#base=${SHA}%5E)`);
    expect(details).toEqual([{ path: ABS, autoDiff: false, baseRef: `${SHA}^` }]);
  });

  it('accepts a full 40-char sha without ^', () => {
    const full = 'a'.repeat(40);
    clickLink(`[x](${ABS}#base=${full})`);
    expect(details).toEqual([{ path: ABS, autoDiff: false, baseRef: full }]);
  });

  it.each(['../x', 'HEAD', ';rm', 'abc12', `${'a'.repeat(41)}`, `${SHA}^^`, '%E0%A4%A'])(
    'invalid ref #base=%s → no baseRef, path still dispatched',
    (ref) => {
      clickLink(`[x](${ABS}#base=${ref})`);
      expect(details).toEqual([{ path: ABS }]);
      expect(openExternal).not.toHaveBeenCalled();
    },
  );

  it('non-base fragment is stripped and ignored', () => {
    clickLink(`[x](${ABS}#L42)`);
    expect(details).toEqual([{ path: ABS }]);
  });

  it('relative ./ path with fragment → ./ and fragment both stripped', () => {
    clickLink(`[x](./backend/core/x.py#base=${SHA}^)`);
    expect(details).toEqual([{ path: 'backend/core/x.py', autoDiff: false, baseRef: `${SHA}^` }]);
  });

  it('link without fragment → unchanged legacy { path } detail', () => {
    clickLink(`[x](${ABS})`);
    expect(details).toEqual([{ path: ABS }]);
  });

  it('external URL (with a fragment) is unchanged: no href, opened externally, no event', () => {
    const url = 'https://example.com/docs/page.html#base=abc1234';
    const { a } = clickLink(`[ext](${url})`);
    expect(a.getAttribute('href')).toBeNull();
    expect(a.getAttribute('role')).toBe('link');
    expect(a.getAttribute('title')).toBe(url);
    expect(details).toEqual([]);
    expect(openExternal).toHaveBeenCalledWith(url);
  });
});

describe('inline code path with #base=<ref>', () => {
  it('valid fragment → clickable, path stripped + baseRef + autoDiff:false', () => {
    const { code } = clickCode(`see \`${ABS}#base=${SHA}^\``);
    expect(code.getAttribute('role')).toBe('button');
    expect(details).toEqual([{ path: ABS, autoDiff: false, baseRef: `${SHA}^` }]);
  });

  it('%5E in inline code is decoded', () => {
    clickCode(`\`backend/core/x.py#base=${SHA}%5E\``);
    expect(details).toEqual([{ path: 'backend/core/x.py', autoDiff: false, baseRef: `${SHA}^` }]);
  });

  it.each(['../x', 'HEAD', ';rm'])('invalid ref #base=%s → plain { path }', (ref) => {
    clickCode(`\`${ABS}#base=${ref}\``);
    expect(details).toEqual([{ path: ABS }]);
  });

  it('non-base fragment stripped; no-fragment path unchanged', () => {
    clickCode(`\`backend/core/x.py#L10\``);
    expect(details).toEqual([{ path: 'backend/core/x.py' }]);
  });

  it('Enter key dispatches the same detail', () => {
    const { container } = render(<MarkdownRenderer content={`\`${ABS}#base=${SHA}^\``} />);
    fireEvent.keyDown(container.querySelector('code')!, { key: 'Enter' });
    expect(details).toEqual([{ path: ABS, autoDiff: false, baseRef: `${SHA}^` }]);
  });
});

describe('parseOpenFileTarget / buildOpenFileDetail (pure)', () => {
  it('no fragment → path as-is, no baseRef', () => {
    expect(parseOpenFileTarget('a/b.py')).toEqual({ path: 'a/b.py' });
    expect(buildOpenFileDetail('./a/b.py')).toEqual({ path: 'a/b.py' });
  });

  it('malformed %-escape in the ref is ignored, never throws', () => {
    expect(parseOpenFileTarget('a/b.py#base=%E0%A4%A')).toEqual({ path: 'a/b.py' });
  });

  it('base= must be the whole fragment key (no prefix tricks)', () => {
    expect(parseOpenFileTarget(`a/b.py#xbase=${SHA}`)).toEqual({ path: 'a/b.py' });
    expect(parseOpenFileTarget(`a/b.py#base=${SHA}&x=1`)).toEqual({ path: 'a/b.py' });
  });

  it('uppercase hex accepted', () => {
    expect(parseOpenFileTarget('a/b.py#base=ABCDEF0^')).toEqual({ path: 'a/b.py', baseRef: 'ABCDEF0^' });
  });
  it('path is URL-decoded (spaces, non-ASCII), with and without a fragment', () => {
    expect(parseOpenFileTarget('/Users/me/my%20dir/a%20b.md')).toEqual({ path: '/Users/me/my dir/a b.md' });
    expect(parseOpenFileTarget('/Users/me/%E6%8A%A5%E5%91%8A.md#base=abc1234%5E'))
      .toEqual({ path: '/Users/me/报告.md', baseRef: 'abc1234^' });
  });
  it('malformed %-escape in the path keeps the raw path, never throws', () => {
    expect(parseOpenFileTarget('a/50%off.py')).toEqual({ path: 'a/50%off.py' });
    expect(parseOpenFileTarget('a/%E0%A4%A.py#L1')).toEqual({ path: 'a/%E0%A4%A.py' });
  });
  it('%23 stays in the path (split on # happens before decoding)', () => {
    expect(parseOpenFileTarget('a/x%23y.py#base=abc1234')).toEqual({ path: 'a/x#y.py', baseRef: 'abc1234' });
  });
  it('encoded traversal is still rejected after decoding', () => {
    expect(isWorkspaceFilePath(parseOpenFileTarget('/Users/me/%2e%2e/%2e%2e/etc/x.py').path)).toBe(false);
  });
});

describe('links with spaces / non-ASCII open the decoded path', () => {
  it('angle-bracket link with spaces dispatches the decoded absolute path', () => {
    clickLink('[my file.md](</Users/me/my dir/my file.md>)');
    expect(details).toEqual([{ path: '/Users/me/my dir/my file.md' }]);
    expect(openExternal).not.toHaveBeenCalled();
  });
  it('non-ASCII filename with #base is decoded and keeps baseRef', () => {
    clickLink(`[报告.md](/Users/me/repo/报告.md#base=${SHA}^)`);
    expect(details).toEqual([{ path: '/Users/me/repo/报告.md', autoDiff: false, baseRef: `${SHA}^` }]);
  });
});
