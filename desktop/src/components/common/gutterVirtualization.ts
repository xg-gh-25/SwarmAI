/**
 * gutterVirtualization — shared virtual-scroll math for the editor line gutters.
 *
 * Extracted from FileEditorCore to break a circular import: FileEditorCore
 * imports the ReviewModeGutter COMPONENT, and ReviewModeGutter needs this
 * window math — importing it back from FileEditorCore created a cycle. This is
 * a leaf module (no component imports), so both gutters depend on it and the
 * cycle is gone.
 *
 * Pure logic + constants only — no React, no DOM.
 */

/** Editor line height in px — matches the textarea's leading-6 (24px). */
export const GUTTER_LINE_HEIGHT = 24;

/**
 * Above this line count, gutters virtualize (render only the visible window).
 * Below it, they render every line — the un-virtualized path is byte-for-byte
 * unchanged, so normal-sized files (the common case) have zero behavior change.
 *
 * NOTE: 2000 is a CONSERVATIVE SAFE value (virtualize early rather than late),
 * not an empirically measured cutover point. The real "gutter starts to jank at
 * N lines" threshold has not been profiled (SOUL P3 / PIT28 measure-before-
 * mechanism) — picking low is safe because the virtualized path is visually
 * identical to the full path. Follow-up: profile the actual jank onset and
 * raise this if 2000 is needlessly conservative.
 */
export const GUTTER_VIRTUALIZE_MIN_LINES = 2_000;

/** Extra lines rendered above+below the viewport so fast scroll shows no gap. */
export const GUTTER_OVERSCAN = 40;

/** Viewport-height fallback when the real height is unavailable (initial mount,
 *  or jsdom where clientHeight is 0) — renders a sensible first window. */
export const GUTTER_FALLBACK_VIEWPORT_PX = 1_200;

/**
 * Compute the visible line-number window [start, end) for a virtualized gutter.
 * Pure — exported for testing. `end` is exclusive. Falls back to a default
 * viewport height when `viewportHeight` is 0 so the first paint is never empty.
 */
export function computeGutterWindow(
  lineCount: number,
  scrollTop: number,
  viewportHeight: number,
): { start: number; end: number } {
  const vh = viewportHeight > 0 ? viewportHeight : GUTTER_FALLBACK_VIEWPORT_PX;
  const visible = Math.ceil(vh / GUTTER_LINE_HEIGHT) + GUTTER_OVERSCAN * 2;
  // Clamp start into [0, lineCount] BEFORE computing end. Without the upper
  // clamp, a scrollTop past the file's end (defensive: shouldn't happen since
  // the textarea bounds scroll, but a stale scrollTop during a shrink can) makes
  // start > end → Array.from({length: end-start}) gets a negative length.
  const rawStart = Math.floor(scrollTop / GUTTER_LINE_HEIGHT) - GUTTER_OVERSCAN;
  const start = Math.min(Math.max(0, rawStart), lineCount);
  const end = Math.min(lineCount, start + visible);
  return { start, end };
}
