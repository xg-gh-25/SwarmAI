/**
 * Streamed markdown must not flicker while it grows.
 *
 * - mermaid: a diagram whose source is still being written does not parse; while the
 *   text streams that is not an error. The last good diagram (or the loading line)
 *   stays, and the red error box appears only if the FINAL source fails.
 * - code: highlight.js recolours a block after React rewrites its text. A grown block
 *   must not paint a frame of plain text between two coloured ones.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import { ContentBlockRenderer } from '../ContentBlockRenderer';
import type { ContentBlock } from '../../../../types';

vi.mock('../../../../services/tauri', () => ({ getApiBaseUrl: () => 'http://localhost:18321' }));
vi.mock('../../../../contexts/ThemeContext', () => ({ useTheme: () => ({ resolvedTheme: 'light' }) }));
vi.mock('mermaid', () => ({
  default: {
    initialize: vi.fn(),
    render: vi.fn(async (_id: string, chart: string) => {
      if (/-->\s*$/.test(chart)) throw new Error('Parse error: unexpected end');
      return { svg: `<svg data-chart="${chart.length}"></svg>` };
    }),
  },
}));
vi.mock('highlight.js', () => ({
  default: {
    configure: vi.fn(),
    highlightElement: vi.fn((el: HTMLElement) => {
      el.innerHTML = `<span class="hl">${el.textContent ?? ''}</span>`;
      el.setAttribute('data-highlighted', 'yes');
    }),
  },
}));

afterEach(() => { vi.useRealTimers(); });

const md = (text: string): ContentBlock => ({ type: 'text', text }) as ContentBlock;
const view = (text: string, isStreaming: boolean) => <ContentBlockRenderer block={md(text)} isStreaming={isStreaming} />;
const GOOD = '```mermaid\ngraph TD\nA-->B\n```';
const PARTIAL = '```mermaid\ngraph TD\nA-->B\nB-->\n```';

describe('mermaid while streaming', () => {
  it('keeps the last good diagram for a half-written source, and reports only a final failure', async () => {
    const { rerender, container } = render(view(GOOD, true));
    await act(async () => {});
    expect(container.querySelector('svg')).not.toBeNull();

    await act(async () => { await new Promise((r) => setTimeout(r, 250)); });
    rerender(view(PARTIAL, true));
    await act(async () => {});
    expect(screen.queryByText('Mermaid Diagram Error')).toBeNull();
    expect(container.querySelector('svg')).not.toBeNull();

    rerender(view(PARTIAL, false));
    await act(async () => {});
    expect(screen.getByText('Mermaid Diagram Error')).toBeTruthy();
  });
});

describe('code while streaming', () => {
  it('a grown block is coloured in the same frame its text changes', () => {
    vi.useFakeTimers();
    const { rerender, container } = render(view('```ts\nconst a = 1;\n```', true));
    act(() => { vi.advanceTimersByTime(1); }); // first paint: deferred highlight
    expect(container.querySelector('code .hl')).not.toBeNull();

    act(() => { vi.advanceTimersByTime(250); }); // past the stream throttle window
    rerender(view('```ts\nconst a = 1;\nconst b = 2;\n```', true));
    const code = container.querySelector('code')!;
    expect(code.textContent).toContain('const b = 2;');
    expect(code.querySelector('.hl')).not.toBeNull();
  });
});
