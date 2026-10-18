/**
 * StreamingBee — the streaming indicator (flying bee / resting bee).
 *
 * What is tested: flying by default (decorative SVG, aria-hidden, no text);
 * a mounted-but-empty polite live region; resting puts the "{name} is mulling it over…" line in it naming
 * ASSISTANT_NAME, with no digits and no stuck/fail/soon wording; under
 * prefers-reduced-motion the still variant replaces the flying one; a missing
 * window.matchMedia (jsdom default) does not throw.
 *
 * Methodology: render with a pass-through i18n mock; matchMedia stubbed per test.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, act } from '@testing-library/react';
import { StreamingBee } from '../StreamingBee';
import { ASSISTANT_NAME } from '../../assistantName';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, o?: { defaultValue?: string }) => o?.defaultValue ?? k }),
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
});

describe('StreamingBee', () => {
  it('flying: decorative aria-hidden SVG, no status text', () => {
    stubReducedMotion(false);
    render(<StreamingBee resting={false} />);
    const root = screen.getByTestId('streaming-bee');
    expect(root.className).toContain('streaming-bee--flying');
    expect(root.className).not.toContain('streaming-bee--resting');
    const svg = root.querySelector('svg');
    expect(svg).not.toBeNull();
    expect(svg!.getAttribute('aria-hidden')).toBe('true');
    // The live region is mounted but empty, so the text later ENTERS it.
    const region = screen.getByRole('status');
    expect(region.getAttribute('aria-live')).toBe('polite');
    expect(region.textContent).toBe('');
  });

  it('resting: status line names the assistant, no seconds / stuck / soon', () => {
    stubReducedMotion(false);
    render(<StreamingBee resting />);
    const root = screen.getByTestId('streaming-bee');
    expect(root.className).toContain('streaming-bee--resting');
    expect(root.className).not.toContain('streaming-bee--flying');
    const status = screen.getByRole('status');
    expect(status.getAttribute('aria-live')).toBe('polite');
    expect(status.textContent).toBe(`${ASSISTANT_NAME} is mulling it over…`);
    expect(status.textContent).not.toMatch(/\d/);
    expect(status.textContent).not.toMatch(/stuck|fail|soon/i);
  });

  it('reduced motion: still variant, never flying', () => {
    stubReducedMotion(true);
    render(<StreamingBee resting={false} />);
    const root = screen.getByTestId('streaming-bee');
    expect(root.className).toContain('streaming-bee--still');
    expect(root.className).not.toContain('streaming-bee--flying');
  });

  it('reduced motion still shows the resting line when resting', () => {
    stubReducedMotion(true);
    render(<StreamingBee resting />);
    expect(screen.getByTestId('streaming-bee').className).toContain('streaming-bee--still');
    expect(screen.getByRole('status').textContent).toContain(ASSISTANT_NAME);
  });

  it('follows an OS reduced-motion change while mounted', () => {
    stubReducedMotion(false);
    render(<StreamingBee resting={false} />);
    expect(screen.getByTestId('streaming-bee').className).toContain('streaming-bee--flying');
    act(() => { currentMatches = true; mqlListener?.(); });
    expect(screen.getByTestId('streaming-bee').className).toContain('streaming-bee--still');
  });

  it('no window.matchMedia (jsdom default) → renders flying, does not throw', () => {
    // @ts-expect-error simulate an environment without matchMedia
    window.matchMedia = undefined;
    render(<StreamingBee resting={false} />);
    expect(screen.getByTestId('streaming-bee').className).toContain('streaming-bee--flying');
  });
});
