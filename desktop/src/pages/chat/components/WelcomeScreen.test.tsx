/**
 * WelcomeScreen.test.tsx — the v2 Welcome screen (design welcome-screen-v2.md).
 *
 * What is tested (acceptance tests, written before the rewrite):
 *   - Brand: logo, the SwarmAI wordmark and the slogan render.
 *   - One queue: the "Needs you" count is the attention query's blocking+review,
 *     at most 3 rows, and the card reads the shared ['overlay','needs-you'] key.
 *   - Rows: each shows the backend's plain title + summary; clicking the text
 *     fills the chat input with the item's prompt (not sent), ↑ sends it now;
 *     "N more" expands the list in place (bounded height, scrolls inside the
 *     card) and collapses again; "Open all" opens the Needs You overlay.
 *   - No dead reads: rendering issues no /system/briefing or /ddd/brains request.
 *   - Empty queue shows one calm line; a failed read hides the section.
 * Methodology: render the real component in a real QueryClientProvider with the
 * attention service and the api client mocked.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const fetchAttention = vi.fn();
vi.mock('../../../services/attention', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  attentionService: { fetchAttention: () => fetchAttention() },
}));

const apiGet = vi.fn(async () => ({ data: {} }));
vi.mock('../../../services/api', () => ({ default: { get: (...a: unknown[]) => apiGet(...a), post: vi.fn() } }));

const dispatchUiCommand = vi.fn(() => true);
vi.mock('../../../utils/uiCommands', () => ({ dispatchUiCommand: (...a: unknown[]) => dispatchUiCommand(...a) }));

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k }) }));

import { WelcomeScreen as WelcomeScreenImpl } from './WelcomeScreen';
import { useAttentionQueue } from '../../../hooks/useAttentionQueue';
import { landedTransform } from './welcomeBee';
import { INJECT_CHAT_INPUT, type InjectChatInputDetail } from '../injectChatInput';

// Extra props (onFocusClick / onItemClick) are passed on purpose: the old screen
// took them and sent on click; the new one must ignore them.
const WelcomeScreen = WelcomeScreenImpl as React.ComponentType<Record<string, unknown>>;

const item = (i: number, brain: string | null = 'SwarmAI') => ({
  id: `a${i}`, source: 'paused_run' as const, tier: 'blocking' as const, brain,
  title: `Item ${i}`, summary: `Summary ${i}`, detail: '', dispatch: { message: `Prompt for item ${i}` },
});

let injected: InjectChatInputDetail[] = [];
const onInject = (e: Event) => injected.push((e as CustomEvent<InjectChatInputDetail>).detail);

function renderWelcome(props: Record<string, unknown> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return { qc, ...render(
    <QueryClientProvider client={qc}><WelcomeScreen {...props} /></QueryClientProvider>,
  ) };
}

beforeEach(() => {
  fetchAttention.mockReset(); apiGet.mockClear(); dispatchUiCommand.mockClear();
  injected = []; window.addEventListener(INJECT_CHAT_INPUT, onInject);
});
afterEach(() => { window.removeEventListener(INJECT_CHAT_INPUT, onInject); cleanup(); });

describe('WelcomeScreen v2', () => {
  it('shows the logo, the wordmark and the slogan', () => {
    fetchAttention.mockResolvedValue({ items: [], counts: { blocking: 0, review: 0 } });
    renderWelcome();
    expect(screen.getByAltText('SwarmAI')).toBeTruthy();
    expect(screen.getByTestId('welcome-wordmark').textContent).toBe('SwarmAI');
    expect(screen.getByTestId('welcome-slogan').textContent).toBe('Grow your Brain. Deliver Anything.');
  });

  it('count = blocking + review from the attention query; at most 3 rows', async () => {
    fetchAttention.mockResolvedValue({
      items: [1, 2, 3, 4, 5].map((i) => item(i)),
      counts: { blocking: 5, review: 3 },
    });
    renderWelcome();
    await waitFor(() => expect(screen.getByTestId('welcome-needs-count').textContent).toBe('8'));
    expect(screen.getAllByTestId('welcome-needs-row')).toHaveLength(3);
    expect(screen.getByTestId('welcome-needs-more').textContent).toContain('5');
  });

  it('reads the shared needs-you query key (one cache with the pill and overlay)', async () => {
    fetchAttention.mockResolvedValue({ items: [item(1)], counts: { blocking: 1, review: 0 } });
    const { qc } = renderWelcome();
    await waitFor(() => expect(screen.getAllByTestId('welcome-needs-row')).toHaveLength(1));
    expect(qc.getQueryData(['overlay', 'needs-you'])).toBeTruthy();
  });

  it('the pill hook and the Welcome card share one request and one number', async () => {
    fetchAttention.mockResolvedValue({ items: [item(1)], counts: { blocking: 4, review: 4 } });
    let pillCount = -1;
    function Pill() {
      const { counts } = useAttentionQueue();
      pillCount = counts.blocking + counts.review;
      return null;
    }
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={qc}><Pill /><WelcomeScreen /></QueryClientProvider>);
    await waitFor(() => expect(screen.getByTestId('welcome-needs-count').textContent).toBe('8'));
    expect(pillCount).toBe(8);
    expect(fetchAttention).toHaveBeenCalledTimes(1);
  });

  it('a row shows the backend title and summary as-is, with a plain source label', async () => {
    fetchAttention.mockResolvedValue({ items: [item(1)], counts: { blocking: 1, review: 0 } });
    renderWelcome();
    const row = await screen.findByTestId('welcome-needs-row');
    expect(row.textContent).toContain('Item 1');
    expect(row.textContent).toContain('Summary 1');
    expect(row.textContent).toContain('Pipeline');
  });

  it('clicking a row fills the input with its prompt and does not send', async () => {
    fetchAttention.mockResolvedValue({ items: [item(1)], counts: { blocking: 1, review: 0 } });
    const onFocusClick = vi.fn();
    const onItemClick = vi.fn();
    renderWelcome({ onFocusClick, onItemClick });
    fireEvent.click(await screen.findByTestId('welcome-needs-row-fill'));
    expect(injected).toEqual([{ text: 'Prompt for item 1', focus: true, autoSend: false }]);
    expect(dispatchUiCommand).not.toHaveBeenCalled();
    expect(onFocusClick).not.toHaveBeenCalled();
    expect(onItemClick).not.toHaveBeenCalled();
  });

  it('the ↑ button sends the prompt now', async () => {
    fetchAttention.mockResolvedValue({ items: [item(1)], counts: { blocking: 1, review: 0 } });
    renderWelcome();
    fireEvent.click(await screen.findByTestId('welcome-needs-row-send'));
    expect(injected).toEqual([{ text: 'Prompt for item 1', focus: true, autoSend: true }]);
  });

  it('"N more" expands in place inside a bounded list, and collapses again', async () => {
    fetchAttention.mockResolvedValue({
      items: [1, 2, 3, 4, 5, 6, 7, 8].map((i) => item(i)),
      counts: { blocking: 8, review: 0 },
    });
    renderWelcome();
    await waitFor(() => expect(screen.getAllByTestId('welcome-needs-row')).toHaveLength(3));
    const more = screen.getByTestId('welcome-needs-more');
    expect(more.getAttribute('aria-expanded')).toBe('false');
    fireEvent.click(more);
    expect(screen.getAllByTestId('welcome-needs-row')).toHaveLength(8);
    expect(more.getAttribute('aria-expanded')).toBe('true');
    // the expanded list scrolls inside the card instead of growing the page
    expect(screen.getByTestId('welcome-needs-list').className).toMatch(/max-h-\[[^\]]+\].*overflow-y-auto|overflow-y-auto.*max-h-\[[^\]]+\]/);
    fireEvent.click(more);
    expect(screen.getAllByTestId('welcome-needs-row')).toHaveLength(3);
    expect(dispatchUiCommand).not.toHaveBeenCalled();
  });

  it('Open all opens the Needs You overlay', async () => {
    fetchAttention.mockResolvedValue({ items: [item(1)], counts: { blocking: 1, review: 0 } });
    renderWelcome();
    fireEvent.click(await screen.findByTestId('welcome-needs-open'));
    expect(dispatchUiCommand).toHaveBeenCalledWith('show-needs-you');
    expect(injected).toEqual([]);
  });

  it('issues no /system/briefing or /ddd/brains request', async () => {
    fetchAttention.mockResolvedValue({ items: [item(1)], counts: { blocking: 1, review: 0 } });
    renderWelcome();
    await screen.findByTestId('welcome-needs-row');
    const urls = apiGet.mock.calls.map((c) => String(c[0]));
    expect(urls.filter((u) => /briefing|ddd\/brains/.test(u))).toEqual([]);
  });

  it('an empty queue shows one calm line', async () => {
    fetchAttention.mockResolvedValue({ items: [], counts: { blocking: 0, review: 0 } });
    renderWelcome();
    expect(await screen.findByTestId('welcome-needs-empty')).toBeTruthy();
    expect(screen.queryByTestId('welcome-needs-row')).toBeNull();
  });

  it('a failed read hides the section (the screen still works as a chat start)', async () => {
    fetchAttention.mockRejectedValue(new Error('down'));
    renderWelcome();
    await waitFor(() => expect(fetchAttention).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByTestId('welcome-needs')).toBeNull();
    expect(screen.getByTestId('welcome-wordmark')).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// The bee: flies in once (3.6s), lands on the logo; hovering the landed logo
// sends it on one 2.6s lap. Reduced motion / no WAAPI / a hidden tab: it just
// sits on the landing spot and nothing animates.
// ---------------------------------------------------------------------------

describe('WelcomeScreen bee', () => {
  type FakeAnim = { cancel: ReturnType<typeof vi.fn>; onfinish: (() => void) | null };
  let anims: Array<{ frames: Keyframe[]; opts: KeyframeAnimationOptions; a: FakeAnim }>;
  const origAnimate = (HTMLElement.prototype as { animate?: unknown }).animate;
  const origRect = HTMLElement.prototype.getBoundingClientRect;
  const origMatch = window.matchMedia;

  function useAnimate(width = 800) {
    anims = [];
    (HTMLElement.prototype as { animate?: unknown }).animate = function (frames: Keyframe[], opts: KeyframeAnimationOptions) {
      const a: FakeAnim = { cancel: vi.fn(), onfinish: null };
      anims.push({ frames, opts, a });
      return a;
    };
    HTMLElement.prototype.getBoundingClientRect = () =>
      ({ left: 0, top: 0, width, height: 600, right: width, bottom: 600, x: 0, y: 0, toJSON: () => ({}) }) as DOMRect;
  }
  function reducedMotion(on: boolean) {
    window.matchMedia = ((q: string) => ({
      matches: on && q.includes('reduce'), media: q,
      addEventListener: () => {}, removeEventListener: () => {},
    })) as unknown as typeof window.matchMedia;
  }

  beforeEach(() => {
    fetchAttention.mockResolvedValue({ items: [], counts: { blocking: 0, review: 0 } });
    reducedMotion(false);
  });
  afterEach(() => {
    (HTMLElement.prototype as { animate?: unknown }).animate = origAnimate;
    HTMLElement.prototype.getBoundingClientRect = origRect;
    window.matchMedia = origMatch;
  });

  const bee = () => screen.getByTestId('welcome-bee');

  it('flies in once on mount (3.6s) and ends exactly on the landing spot', async () => {
    useAnimate();
    renderWelcome();
    expect(anims).toHaveLength(1);
    expect(anims[0].opts.duration).toBe(3600);
    expect(anims[0].frames[anims[0].frames.length - 1].transform).toBe(landedTransform);
    expect(bee().className).toContain('welcome-bee--flying');
    act(() => anims[0].a.onfinish?.());
    expect(bee().className).toContain('welcome-bee--landed');
    expect(bee().className).not.toContain('welcome-bee--flying');
  });

  it('hovering the landed logo sends it on one 2.6s lap; hovering mid-flight does not', () => {
    useAnimate();
    renderWelcome();
    fireEvent.mouseEnter(screen.getByTestId('welcome-logo'));
    expect(anims).toHaveLength(1);                     // still flying in: ignored
    act(() => anims[0].a.onfinish?.());
    fireEvent.mouseEnter(screen.getByTestId('welcome-logo'));
    expect(anims).toHaveLength(2);
    expect(anims[1].opts.duration).toBe(2600);
    expect(anims[1].frames[0].transform).toBe(landedTransform);
  });

  it('reduced motion: no flight, no lap, the bee sits on the logo', () => {
    useAnimate();
    reducedMotion(true);
    renderWelcome();
    fireEvent.mouseEnter(screen.getByTestId('welcome-logo'));
    expect(anims).toHaveLength(0);
    expect(bee().className).toContain('welcome-bee--landed');
  });

  it('a hidden tab (zero width) does not fly; it is already landed when shown', () => {
    useAnimate(0);
    renderWelcome();
    expect(anims).toHaveLength(0);
    expect(bee().className).toContain('welcome-bee--landed');
  });

  it('without Web Animations (jsdom) the bee renders landed and nothing throws', () => {
    (HTMLElement.prototype as { animate?: unknown }).animate = undefined;
    renderWelcome();
    expect(bee().className).toContain('welcome-bee--landed');
  });

  it('unmounting mid-flight cancels the animation', () => {
    useAnimate();
    const { unmount } = renderWelcome();
    unmount();
    expect(anims[0].a.cancel).toHaveBeenCalled();
  });
});
