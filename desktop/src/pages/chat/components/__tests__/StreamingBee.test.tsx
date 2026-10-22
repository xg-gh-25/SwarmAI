/**
 * StreamingBee — the streaming indicator and its 45s wait hint.
 *
 * What is tested: the bee flies while the turn works and never lands (no resting
 * variant, also after a long quiet); nothing is said before 45s; after it, one muted
 * line states the fact: the running call with its elapsed time, background agents,
 * the model thinking (thinking_progress), else waiting for the model; new content
 * hides it at once; the polite live region announces the wait once, without the
 * ticking clock; reduced motion gives the still variant; no matchMedia does not throw.
 *
 * Methodology: render with a pass-through i18n mock; matchMedia stubbed per test.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, act } from '@testing-library/react';
import { StreamingBee } from '../StreamingBee';
import { WAIT_HINT_MS } from '../../../../hooks/useStreamingWait';
import { backgroundAgentsStore } from '../../../../stores/backgroundAgentsStore';
import { clearThinking, noteThinking } from '../../../../stores/thinkingProgressStore';
import type { ContentBlock } from '../../../../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, fallback: unknown) => fallback, i18n: { language: 'en' } }),
}));

const realMatchMedia = window.matchMedia;
let mqlListener: (() => void) | null = null;
let currentMatches = false;
function stubReducedMotion(matches: boolean) {
  currentMatches = matches;
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    get matches() { return query.includes('prefers-reduced-motion') ? currentMatches : false; },
    media: query,
    addEventListener: vi.fn((_: string, cb: () => void) => { mqlListener = cb; }),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    onchange: null,
    dispatchEvent: vi.fn(),
  })) as unknown as typeof window.matchMedia;
}

afterEach(() => {
  cleanup();
  window.matchMedia = realMatchMedia;
  vi.useRealTimers();
  clearThinking();
  backgroundAgentsStore.apply('s1', []);
});

const bash = (id: string, cmd: string): ContentBlock =>
  ({ type: 'tool_use', id, name: 'Bash', summary: `Running: ${cmd}`, category: 'bash' }) as ContentBlock;
const tr = (id: string): ContentBlock => ({ type: 'tool_result', toolUseId: id, content: 'ok', isError: false, truncated: false }) as ContentBlock;
const tx = (text: string): ContentBlock => ({ type: 'text', text }) as ContentBlock;
const hint = () => document.querySelector('.streaming-bee-note-text')?.textContent ?? null;
const cls = () => screen.getByTestId('streaming-bee').className;

describe('StreamingBee', () => {
  it('flying: decorative aria-hidden SVG, a silent live region, no hint yet', () => {
    stubReducedMotion(false);
    render(<StreamingBee content={[tx('go')]} sessionId="s1" />);
    expect(cls()).toContain('streaming-bee--flying');
    expect(screen.getByTestId('streaming-bee').querySelector('svg')!.getAttribute('aria-hidden')).toBe('true');
    const region = screen.getByRole('status');
    expect(region.getAttribute('aria-live')).toBe('polite');
    expect(region.textContent).toBe('');
    expect(hint()).toBeNull();
  });

  it('a running call: nothing before 45s, then the call and its time; the bee keeps flying', () => {
    vi.useFakeTimers();
    stubReducedMotion(false);
    const content = [tx('go'), bash('a', 'make test')];
    render(<StreamingBee content={content} sessionId="s1" />);
    act(() => { vi.advanceTimersByTime(WAIT_HINT_MS - 1000); });
    expect(hint()).toBeNull();
    act(() => { vi.advanceTimersByTime(1000); });
    expect(hint()).toBe('Bash · make test · 45s');
    act(() => { vi.advanceTimersByTime(27_000); });
    expect(hint()).toBe('Bash · make test · 1m 12s');
    expect(cls()).toContain('streaming-bee--flying');
    expect(cls()).not.toMatch(/resting/);
    // announced once, without the ticking clock
    expect(screen.getByRole('status').textContent).toBe('Bash · make test');
  });

  it('new content hides the hint at once and restarts the 45s', () => {
    vi.useFakeTimers();
    stubReducedMotion(false);
    const first = [tx('go'), bash('a', 'make test')];
    const r = render(<StreamingBee content={first} sessionId="s1" />);
    act(() => { vi.advanceTimersByTime(WAIT_HINT_MS); });
    expect(hint()).not.toBeNull();
    r.rerender(<StreamingBee content={[...first, tr('a'), tx('done, next')]} sessionId="s1" />);
    expect(hint()).toBeNull();
  });

  it('no call running, background agents still running: says so', () => {
    vi.useFakeTimers();
    stubReducedMotion(false);
    act(() => { backgroundAgentsStore.apply('s1', [
      { toolUseId: 'g1', status: 'running', startedAt: Date.now() },
      { toolUseId: 'g2', status: 'running', startedAt: Date.now() },
    ]); });
    render(<StreamingBee content={[tx('two reviews started')]} sessionId="s1" />);
    act(() => { vi.advanceTimersByTime(WAIT_HINT_MS); });
    expect(hint()).toBe('Waiting for 2 background agents · 45s');
  });

  it('thinking_progress arriving: Thinking, timed from when the think began', () => {
    vi.useFakeTimers();
    stubReducedMotion(false);
    render(<StreamingBee content={[tx('hm')]} sessionId="s1" />);
    act(() => { vi.advanceTimersByTime(WAIT_HINT_MS - 5000); });
    noteThinking('s1', 50);
    act(() => { vi.advanceTimersByTime(5000); });
    expect(hint()).toBe('Thinking · 55s');
  });

  it('nothing else known: waiting for the model', () => {
    vi.useFakeTimers();
    stubReducedMotion(false);
    render(<StreamingBee content={[tx('hm')]} sessionId="s1" />);
    act(() => { vi.advanceTimersByTime(WAIT_HINT_MS); });
    expect(hint()).toBe('Waiting for the model · 45s');
    expect(hint()).not.toMatch(/stuck|fail|soon|mulling/i);
  });

  it('reduced motion: still variant, never flying', () => {
    stubReducedMotion(true);
    render(<StreamingBee content={[]} />);
    expect(cls()).toContain('streaming-bee--still');
    expect(cls()).not.toContain('streaming-bee--flying');
  });

  it('follows an OS reduced-motion change while mounted', () => {
    stubReducedMotion(false);
    render(<StreamingBee content={[]} />);
    expect(cls()).toContain('streaming-bee--flying');
    act(() => { currentMatches = true; mqlListener?.(); });
    expect(cls()).toContain('streaming-bee--still');
  });

  it('no window.matchMedia (jsdom default) → renders flying, does not throw', () => {
    // @ts-expect-error simulate an environment without matchMedia
    window.matchMedia = undefined;
    render(<StreamingBee content={[]} />);
    expect(cls()).toContain('streaming-bee--flying');
  });
});
