/**
 * Layout-viewport readers that stay correct under the app's CSS zoom.
 *
 * The app zooms by setting `zoom` on `<html>` (hooks/useZoom.ts). Under that zoom
 * the engine lays the page out in ZOOMED CSS px, while `window.innerWidth`,
 * `innerHeight`, pointer `clientX/clientY` and `getBoundingClientRect()` stay in
 * UNZOOMED client px: a box at `left:100px` reports rect.left 100 × zoom.
 *
 * What `documentElement.clientWidth` reports under that zoom differs by engine
 * version: an earlier WKWebView / Chromium 143 measurement gave the zoomed CSS width
 * (550 on an 1100px window at zoom 2), while WebKit 26.5 and Chromium 149 give the
 * window width unchanged (1400 on a 1400px window at zoom 1.2). Deriving the zoom
 * from clientWidth therefore read 1 on the newer engines and every consumer stopped
 * correcting. So these functions read the zoom itself (`currentCSSZoom`, supported
 * by both engines; the computed `zoom` style as a fallback) and divide client px by
 * it, which is right under either clientWidth behaviour.
 *
 * So a width written to `style.width` (CSS px) must be derived from the layout
 * width, not innerWidth, and a pointer delta or a rect coordinate must be scaled
 * into CSS px before it is used as a CSS length. Use these functions for that.
 * (OverlayHost reads the same 1/zoom from useZoom's `--app-zoom-inv` custom
 * property; both agree as long as zoom is only ever applied on <html>.)
 *
 * Public symbols:
 * - `layoutViewportWidth()` — viewport width in layout CSS px
 * - `layoutViewportHeight()` — viewport height in layout CSS px
 * - `clientToCssScale()`    — CSS px per client px (1 / app zoom)
 * - `APP_ZOOM_EVENT`        — window event useZoom fires after a zoom change
 *
 * CSS zoom fires no `resize` event, so anything that sizes itself from the layout
 * viewport must also listen to APP_ZOOM_EVENT. The name lives here (a side-effect
 * free module) so the sender and every listener import one constant.
 *
 * With no zoom readable (jsdom, the first frame of a Tauri boot) the zoom is 1:
 * innerWidth / innerHeight and scale 1, exactly the behaviour at zoom 1.
 *
 * Assumes <html> has no scrollbar (index.css sets html/body overflow hidden), so
 * innerWidth is the width the page lays out in.
 */

export const APP_ZOOM_EVENT = 'swarm:app-zoom';

/** The effective CSS zoom on <html>; 1 when none is readable. */
function appZoom(): number {
  if (typeof document === 'undefined') return 1;
  const root = document.documentElement as HTMLElement & { currentCSSZoom?: number };
  const raw = typeof root.currentCSSZoom === 'number'
    ? root.currentCSSZoom
    : parseFloat(root.style.zoom || getComputedStyle(root).zoom);
  // currentCSSZoom is a float32 (1.2 reads 1.2000000476837158): round it.
  return Number.isFinite(raw) && raw > 0 ? Math.round(raw * 1e4) / 1e4 : 1;
}

/** Width of the layout viewport in CSS px (what `style.width` values are in). */
export function layoutViewportWidth(): number {
  if (typeof window === 'undefined') return 0;
  return window.innerWidth / appZoom();
}

/** Height of the layout viewport in CSS px (what `style.top` / `style.height` values are in). */
export function layoutViewportHeight(): number {
  if (typeof window === 'undefined') return 0;
  return window.innerHeight / appZoom();
}

/**
 * CSS px per client px: multiply a pointer delta (clientX difference) or a rect
 * coordinate by this to get the CSS px it spans.
 */
export function clientToCssScale(): number {
  if (typeof window === 'undefined') return 1;
  return 1 / appZoom();
}
