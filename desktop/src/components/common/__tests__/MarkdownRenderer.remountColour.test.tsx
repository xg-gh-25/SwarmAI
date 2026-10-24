/**
 * MarkdownRenderer — a code block shown again is coloured in its first frame.
 *
 * What is tested: the first time a code block mounts it may paint plain and colour
 * at idle (long-session open stays fast). A block whose exact text was already
 * coloured in this app session — the same answer moved to another place in the turn,
 * a tab coming back — is coloured BEFORE paint, so it never flashes plain. Before
 * paint here means: right after the render commits, with no timer or idle callback
 * run yet (fake timers stay frozen).
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render } from '@testing-library/react';
import MarkdownRenderer from '../MarkdownRenderer';

afterEach(() => { vi.useRealTimers(); });
const md = (n: number) => `text\n\n\`\`\`ts\nconst seen${n} = ${n} + 1;\n\`\`\`\n`;
const coloured = (c: HTMLElement) => !!c.querySelector('code[data-highlighted="yes"], code.hljs');

describe('code block colour on remount', () => {
  it('a new block may colour after paint; the same block mounted again colours before paint', () => {
    vi.useFakeTimers();
    const first = render(<MarkdownRenderer content={md(1)} />);
    vi.runAllTimers(); // the deferred first colouring
    expect(coloured(first.container)).toBe(true);
    first.unmount();

    const again = render(<MarkdownRenderer content={md(1)} />);
    expect(coloured(again.container)).toBe(true); // no timer has run
  });

  it('a block never seen before still waits for idle (first open stays off the paint path)', () => {
    vi.useFakeTimers();
    const fresh = render(<MarkdownRenderer content={md(2)} />);
    expect(coloured(fresh.container)).toBe(false);
    vi.runAllTimers();
    expect(coloured(fresh.container)).toBe(true);
  });

  it('the same text in another language is a new block (idle), and a huge block seen before still waits for idle', () => {
    vi.useFakeTimers();
    const ts = render(<MarkdownRenderer content={md(3)} />);
    vi.runAllTimers();
    ts.unmount();
    const py = render(<MarkdownRenderer content={md(3).replace('```ts', '```python')} />);
    expect(coloured(py.container)).toBe(false);
    py.unmount();

    const huge = `\`\`\`ts\nconst x = '${'a'.repeat(21_000)}';\n\`\`\`\n`; // > 20k chars
    const a = render(<MarkdownRenderer content={huge} />);
    vi.runAllTimers();
    expect(coloured(a.container)).toBe(true);
    a.unmount();
    const b = render(<MarkdownRenderer content={huge} />);
    expect(coloured(b.container)).toBe(false);
    vi.runAllTimers();
    expect(coloured(b.container)).toBe(true);
  });
});
