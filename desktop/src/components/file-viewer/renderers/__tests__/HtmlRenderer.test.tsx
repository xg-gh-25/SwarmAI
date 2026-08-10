/**
 * HtmlRenderer — inline render via src=<raw endpoint URL> (run_344d1fd6).
 *
 * Bug + fix history: the in-app HTML preview used `<iframe srcDoc={content}>`.
 * In the packaged Tauri WKWebView, srcDoc (a JS→DOM string injection) renders a
 * BLANK frame. Two earlier srcDoc fixes (sandbox, height) were both falsified
 * (commit 496bbd7c). The reliable path in WKWebView is REAL NAVIGATION: point the
 * iframe `src` at the backend raw endpoint (which serves Content-Type: text/html,
 * no Content-Disposition:attachment — verified live), so the WebView loads it as a
 * normal document instead of a srcDoc string. The user wants the report rendered
 * INLINE in Canvas (not a browser jump), so this keeps the iframe — but src=, not
 * srcDoc.
 *
 * Isolation is UNCHANGED: sandbox="allow-scripts" WITHOUT allow-same-origin forces
 * an OPAQUE origin even for a same-origin src URL (MDN-verified) — the frame still
 * cannot reach the parent DOM/cookies/storage. Same risk profile as the old
 * srcDoc+allow-scripts, but it actually renders.
 *
 * Invariants under test:
 *  - default (Rendered) mode renders an iframe whose `src` is the dynamic api base
 *    (NOT hardcoded) + /api/workspace/file/raw + encoded path, NOT srcDoc
 *  - the iframe sandbox has `allow-scripts` but NOT `allow-same-origin` (opaque origin)
 *  - "Open in browser" fallback still calls openExternal with the same URL
 *  - the Source toggle shows the raw HTML markup in-app (no iframe)
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import HtmlRenderer from '../HtmlRenderer';

const mockOpenExternal = vi.fn();
vi.mock('../../../../utils/openExternal', () => ({
  openExternal: (...a: unknown[]) => mockOpenExternal(...a),
}));

/* ------------------------------------------------------------------ *
 *  Fake ResizeObserver (jsdom has none). Captures the latest instance
 *  + its callback so a test can drive a resize and assert disconnect.
 * ------------------------------------------------------------------ */
interface FakeRO {
  cb: ResizeObserverCallback;
  observe: ReturnType<typeof vi.fn>;
  disconnect: ReturnType<typeof vi.fn>;
}
let lastRO: FakeRO | null = null;
class FakeResizeObserver {
  observe = vi.fn();
  disconnect = vi.fn();
  unobserve = vi.fn();
  constructor(public cb: ResizeObserverCallback) {
    lastRO = this as unknown as FakeRO;
  }
}

/** Stub the wrapper element's layout dims so the fit-scale math is deterministic. */
function stubWrapperDims(container: HTMLElement, width: number, height: number) {
  const wrapper = container.querySelector('[data-testid="html-fit-wrapper"]') as HTMLElement | null;
  if (!wrapper) return null;
  Object.defineProperty(wrapper, 'clientWidth', { configurable: true, value: width });
  Object.defineProperty(wrapper, 'clientHeight', { configurable: true, value: height });
  return wrapper;
}

/** Fire the captured ResizeObserver callback (simulates a panel resize). */
function fireResize(wrapper: HTMLElement) {
  act(() => {
    lastRO?.cb(
      [{ target: wrapper, contentRect: {} as DOMRectReadOnly }] as unknown as ResizeObserverEntry[],
      lastRO as unknown as ResizeObserver,
    );
  });
}

// Dynamic api base — mock to a known value so we can assert the exact URL.
vi.mock('../../../../services/tauri', () => ({
  getApiBaseUrl: () => 'http://localhost:18321',
}));

const PROPS = {
  filePath: 'Knowledge/Reports/my report.html', // space → must be encoded
  fileName: 'my report.html',
  content: '<!DOCTYPE html><html><body><h1>Hello</h1></body></html>',
  encoding: 'utf-8' as const,
  mimeType: 'text/html',
  fileSize: 1234,
};

const origRO = globalThis.ResizeObserver;
beforeEach(() => {
  lastRO = null;
  globalThis.ResizeObserver = FakeResizeObserver as unknown as typeof ResizeObserver;
  vi.clearAllMocks();
});
afterEach(() => {
  globalThis.ResizeObserver = origRO;
});

const EXPECTED_RAW_URL = `http://localhost:18321/api/workspace/file/raw?path=${encodeURIComponent(PROPS.filePath)}`;

describe('HtmlRenderer renders HTML inline via src=<raw endpoint URL>', () => {
  it('default mode renders an iframe with src=<raw URL> (NOT srcDoc — the WKWebView blank-frame fix)', () => {
    const { container } = render(<HtmlRenderer {...PROPS} />);
    const iframe = container.querySelector('iframe');
    expect(iframe).not.toBeNull();
    // Real navigation: src points at the raw endpoint (dynamic base + encoded path)…
    expect(iframe!.getAttribute('src')).toBe(EXPECTED_RAW_URL);
    // …and it is NOT the old srcDoc string-injection path (the blank-frame trigger).
    expect(iframe!.hasAttribute('srcdoc')).toBe(false);
    // encodeURIComponent escaped the space in the path.
    expect(iframe!.getAttribute('src')).toContain('my%20report.html');
    expect(iframe!.getAttribute('src')).not.toContain(' ');
  });

  it('iframe sandbox keeps allow-scripts but NOT allow-same-origin (opaque-origin isolation)', () => {
    const { container } = render(<HtmlRenderer {...PROPS} />);
    const sandbox = container.querySelector('iframe')!.getAttribute('sandbox') ?? '';
    expect(sandbox).toContain('allow-scripts');
    expect(sandbox).not.toContain('allow-same-origin');
  });

  it('clicking "Open in browser" calls openExternal once with dynamic base + /api/workspace/file/raw + encoded path', () => {
    render(<HtmlRenderer {...PROPS} />);
    fireEvent.click(screen.getByText('Open in browser'));
    expect(mockOpenExternal).toHaveBeenCalledTimes(1);
    expect(mockOpenExternal).toHaveBeenCalledWith(
      `http://localhost:18321/api/workspace/file/raw?path=${encodeURIComponent(PROPS.filePath)}`,
    );
    // encodeURIComponent must have escaped the space (no raw space in the URL)
    const calledUrl = mockOpenExternal.mock.calls[0][0] as string;
    expect(calledUrl).not.toContain(' ');
    expect(calledUrl).toContain('my%20report.html');
  });

  it('Source toggle shows the raw HTML markup in-app (no iframe path)', () => {
    const { container } = render(<HtmlRenderer {...PROPS} />);
    // toggle button carries a title attr; use it to disambiguate from any
    // "Source" text that may appear elsewhere after toggling
    fireEvent.click(screen.getByTitle('Show HTML source')); // rendered → source
    // markup is escaped into a <pre><code> block
    expect(container.querySelector('pre')).not.toBeNull();
    expect(container.textContent).toContain('DOCTYPE html');
    // still no iframe in source mode
    expect(container.querySelector('iframe')).toBeNull();
  });

  it('shows a graceful message when content is null', () => {
    render(<HtmlRenderer {...PROPS} content={null} />);
    expect(screen.getByText(/No HTML content available/i)).toBeInTheDocument();
  });
});

/* ------------------------------------------------------------------ *
 *  Fit-width mode (run_732236aa) — wide reports fit the panel, no
 *  horizontal scroll. Scale a SIZER div (not the iframe element) at a
 *  fixed logical width; measure with clientWidth (zoom-safe), never
 *  getBoundingClientRect (app <html> CSS-zoom double-count).
 * ------------------------------------------------------------------ */
describe('HtmlRenderer Fit-width mode', () => {
  const LOGICAL_WIDTH = 1200;

  it('Fit is the DEFAULT: a sizer wraps the iframe with a transform: scale(<1) for a narrow panel', () => {
    const { container } = render(<HtmlRenderer {...PROPS} />);
    const wrapper = stubWrapperDims(container, 500, 600)!;
    expect(wrapper).not.toBeNull();
    fireResize(wrapper); // first real measurement (clientWidth=500)
    const sizer = container.querySelector('[data-testid="html-fit-sizer"]') as HTMLElement;
    expect(sizer).not.toBeNull();
    // scale = 500/1200 = 0.4167 — the transform must be present and <1
    expect(sizer.style.transform).toMatch(/scale\(0\.41/);
    expect(sizer.style.transformOrigin).toBe('top left');
    // sizer laid out at the logical width; height = H/s so scaled box = panel H
    expect(sizer.style.width).toBe(`${LOGICAL_WIDTH}px`);
    // the iframe still exists inside the sizer (rendered path preserved)
    expect(sizer.querySelector('iframe')).not.toBeNull();
  });

  it('scale caps at 1 (never upscale) when the panel is wider than the logical width', () => {
    const { container } = render(<HtmlRenderer {...PROPS} />);
    const wrapper = stubWrapperDims(container, 2000, 800)!;
    fireResize(wrapper);
    const sizer = container.querySelector('[data-testid="html-fit-sizer"]') as HTMLElement;
    // 2000/1200 = 1.67 → capped to 1 → scale(1)
    expect(sizer.style.transform).toMatch(/scale\(1\)/);
  });

  it('W<1 is skipped (0-width trap): a zero-width measurement does NOT set scale(0)', () => {
    const { container } = render(<HtmlRenderer {...PROPS} />);
    const wrapper = stubWrapperDims(container, 500, 600)!;
    fireResize(wrapper); // establishes scale(0.41…)
    const sizer = container.querySelector('[data-testid="html-fit-sizer"]') as HTMLElement;
    const before = sizer.style.transform;
    // now a spurious 0-width measurement (mid width-reveal animation)
    Object.defineProperty(wrapper, 'clientWidth', { configurable: true, value: 0 });
    fireResize(wrapper);
    // scale unchanged — never scale(0) (which would vanish the iframe)
    expect(sizer.style.transform).toBe(before);
    expect(sizer.style.transform).not.toMatch(/scale\(0\)/);
  });

  it('Actual toggle removes the transform (iframe fills width:100%, prior behavior)', () => {
    const { container } = render(<HtmlRenderer {...PROPS} />);
    const wrapper = stubWrapperDims(container, 500, 600)!;
    fireResize(wrapper);
    // toggle Fit → Actual
    fireEvent.click(screen.getByTitle('Actual size (no fit-scaling)'));
    // no sizer transform in Actual mode
    const sizer = container.querySelector('[data-testid="html-fit-sizer"]') as HTMLElement | null;
    expect(sizer).toBeNull();
    // iframe is a direct fill child again
    expect(container.querySelector('iframe')).not.toBeNull();
  });

  it('disconnects the ResizeObserver on unmount (no leak)', () => {
    const { container, unmount } = render(<HtmlRenderer {...PROPS} />);
    stubWrapperDims(container, 500, 600);
    expect(lastRO).not.toBeNull();
    unmount();
    expect(lastRO!.disconnect).toHaveBeenCalled();
  });

  it('Source toggle still works from Fit mode (no iframe, shows markup)', () => {
    const { container } = render(<HtmlRenderer {...PROPS} />);
    fireEvent.click(screen.getByTitle('Show HTML source'));
    expect(container.querySelector('iframe')).toBeNull();
    expect(container.querySelector('[data-testid="html-fit-sizer"]')).toBeNull();
    expect(container.textContent).toContain('DOCTYPE html');
  });

  it('does NOT set state on a same-size resize (change-detecting bail avoids rerender churn)', () => {
    const { container } = render(<HtmlRenderer {...PROPS} />);
    const wrapper = stubWrapperDims(container, 500, 600)!;
    fireResize(wrapper); // establishes scale(0.41…)
    const sizer = container.querySelector('[data-testid="html-fit-sizer"]') as HTMLElement;
    const transformBefore = sizer.style.transform;
    // fire an identical-dimension resize (observer fires but dims unchanged)
    fireResize(wrapper);
    fireResize(wrapper);
    const sizerAfter = container.querySelector('[data-testid="html-fit-sizer"]') as HTMLElement;
    // same DOM node (no remount) + unchanged transform: the bail returned prev state
    expect(sizerAfter).toBe(sizer);
    expect(sizerAfter.style.transform).toBe(transformBefore);
    // a CHANGED dimension still updates
    Object.defineProperty(wrapper, 'clientWidth', { configurable: true, value: 900 });
    fireResize(wrapper);
    expect(sizerAfter.style.transform).toMatch(/scale\(0\.75\)/); // 900/1200
  });

  it('re-attaches the ResizeObserver after source→rendered re-entry (fit still tracks resize)', () => {
    const { container } = render(<HtmlRenderer {...PROPS} />);
    // rendered → source: the measured wrapper unmounts, observer disconnects
    fireEvent.click(screen.getByTitle('Show HTML source'));
    const roAfterSource = lastRO;
    expect(roAfterSource!.disconnect).toHaveBeenCalled();
    // source → rendered: wrapper remounts. Effect keyed on [mode] must re-run and
    // create a NEW observer (a [] dep would leave the re-entered view untracked).
    fireEvent.click(screen.getByTitle('Show rendered view'));
    expect(lastRO).not.toBe(roAfterSource); // a fresh observer was created
    // and it actually tracks a resize on the re-entered wrapper
    const wrapper = stubWrapperDims(container, 600, 400)!;
    fireResize(wrapper);
    const sizer = container.querySelector('[data-testid="html-fit-sizer"]') as HTMLElement;
    expect(sizer.style.transform).toMatch(/scale\(0\.5\)/); // 600/1200
  });

  // run_2daacd0f — 6th-recurrence "Canvas 开着时 chat input 输入卡" lag. The composite-
  // heavy Canvas surface (this iframe report + transform:scale sizer) gets WebKit
  // paint/composite isolation via `contain:paint` on the renderer root, so a keystroke
  // repaint in the sibling chat column doesn't force re-compositing it. Scoped HERE
  // (not the Canvas content column) BECAUSE this renderer has no `position:fixed`
  // descendants — the column does (FileEditorCore popover/modal), where paint would
  // re-anchor + clip them. Mutation check: drop `style={{ contain: 'paint' }}` → RED.
  it('the renderer root carries contain:paint (composite isolation, fixed-safe scope)', () => {
    const { container } = render(<HtmlRenderer {...PROPS} />);
    const iframe = container.querySelector('iframe')!;
    // Select by data-testid (not the Tailwind class chain) so a utility-class refactor
    // can't false-negative this guard (Gate-2 finding).
    const root = screen.getByTestId('html-renderer-root') as HTMLElement;
    expect(root).toBeTruthy();
    // Sanity: this really is the root (the iframe surface lives inside it).
    expect(root.contains(iframe)).toBe(true);
    expect(root.style.contain).toBe('paint');
  });
});
