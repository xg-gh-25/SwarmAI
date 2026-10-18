/**
 * Layout-viewport readers that stay correct under the app's CSS zoom.
 *
 * The app zooms by setting `zoom` on `<html>` (hooks/useZoom.ts). Under that zoom
 * the engine lays the page out in ZOOMED CSS px, while `window.innerWidth`, pointer
 * `clientX/clientY` and `getBoundingClientRect()` stay in UNZOOMED client px.
 * Measured in macOS WKWebView and Chromium 143 (the WebView2 engine) at zoom 2 on
 * an 1100px window: innerWidth 1100, documentElement.clientWidth 550, a box at
 * `left:100px` reports rect.left 200, and a pointer at x=400 reports clientX 400.
 *
 * So a width written to `style.width` (CSS px) must be derived from the layout
 * width, not innerWidth, and a pointer delta must be scaled into CSS px before it
 * is added to a CSS width. Use these two functions for that. (OverlayHost reads
 * the same 1/zoom from useZoom's `--app-zoom-inv` custom property; both agree as
 * long as zoom is only ever applied on <html>.)
 *
 * Public symbols:
 * - `layoutViewportWidth()` — viewport width in layout CSS px
 * - `clientToCssScale()`    — CSS px per client px (1 / app zoom)
 * - `APP_ZOOM_EVENT`        — window event useZoom fires after a zoom change
 *
 * CSS zoom fires no `resize` event, so anything that sizes itself from the layout
 * viewport must also listen to APP_ZOOM_EVENT. The name lives here (a side-effect
 * free module) so the sender and every listener import one constant.
 *
 * Both fall back to the unzoomed answer (innerWidth, scale 1) when the document
 * has no layout yet (jsdom, the first frame of a Tauri boot), which is exactly
 * the behaviour at zoom 1.
 *
 * Assumes <html> has no scrollbar (index.css sets html/body overflow hidden):
 * clientWidth excludes a root scrollbar while the rect includes it, so a classic
 * root scrollbar (Windows) would skew the scale by its width even at zoom 1.
 */

export const APP_ZOOM_EVENT = 'swarm:app-zoom';

/** Width of the layout viewport in CSS px (what `style.width` values are in). */
export function layoutViewportWidth(): number {
  if (typeof window === 'undefined') return 0;
  const cw = document.documentElement.clientWidth;
  return cw > 0 ? cw : window.innerWidth;
}

/**
 * CSS px per client px: multiply a pointer delta (clientX difference) by this to
 * get the CSS px it moved across. Measured on `<html>` rather than on a small
 * element because offsetWidth/clientWidth are integers; on a few-px drag handle
 * the rounding error would be tens of percent, on the root it is negligible.
 * (getBoundingClientRect is used here only as the denominator of that ratio — a
 * coordinate-space conversion, not a container measurement.)
 */
export function clientToCssScale(): number {
  if (typeof window === 'undefined') return 1;
  const root = document.documentElement;
  const css = root.clientWidth;
  const client = root.getBoundingClientRect().width;
  return css > 0 && client > 0 ? css / client : 1;
}
