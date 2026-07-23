/**
 * App-wide zoom control via Cmd+Plus / Cmd+Minus / Cmd+0.
 *
 * Applies CSS `zoom` on `<html>` element — works in Tauri webview (WebKit/Chromium).
 * Persists zoom level to localStorage so it survives app restarts.
 *
 * Zoom range: 50% – 200%, step: 10%.
 *
 * Terminal-decoupling contract (do NOT remove without updating TerminalPanel):
 * `applyZoom` publishes TWO things — the raw `zoom` that scales the whole app,
 * AND `--app-zoom-inv` = 1/level, the precomputed RECIPROCAL. The terminal
 * surface reads `--app-zoom-inv` as its counter-zoom (`zoom: var(--app-zoom-inv)`)
 * so the terminal subtree's NET scale stays 1.0 at any app zoom. This is what
 * keeps xterm's mouse-selection accurate: xterm maps a click to a column via
 * (clientX − getBoundingClientRect().left) / cellWidth; under CSS zoom the rect
 * is scaled while cellWidth is not, so a zoomed terminal mis-maps clicks onto
 * blank cells (drift growing rightward). Net-scale 1.0 removes the mismatch.
 * A precomputed reciprocal (not `calc(1/var(--app-zoom))`) sidesteps the
 * `zoom: calc()` support question and is applied as a bare `var()`.
 *
 * The var is published SYNCHRONOUSLY at module load (below) as well as in the
 * hook effect, so the terminal never renders one frame at the wrong scale
 * before React's first effect runs (startup-flash guard).
 */
import { useEffect, useCallback, useState } from 'react';

const STORAGE_KEY = 'swarmai-zoom-level';
const APP_ZOOM_INV_VAR = '--app-zoom-inv';
const MIN_ZOOM = 0.5;
const MAX_ZOOM = 2.0;
const STEP = 0.1;
const DEFAULT_ZOOM = 1.0;

function loadZoom(): number {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored) {
      const val = parseFloat(stored);
      if (!isNaN(val) && val >= MIN_ZOOM && val <= MAX_ZOOM) return val;
    }
  } catch { /* ignore */ }
  return DEFAULT_ZOOM;
}

function applyZoom(level: number) {
  document.documentElement.style.zoom = String(level);
  // Publish the reciprocal for the terminal counter-zoom (see module docstring).
  // level is always > 0 (clamped to [MIN_ZOOM, MAX_ZOOM]), so 1/level is safe.
  document.documentElement.style.setProperty(APP_ZOOM_INV_VAR, String(1 / level));
}

// Startup-flash guard: apply BOTH the raw zoom AND its reciprocal at module
// load, BEFORE React's first render/effect (useEffect fires AFTER first paint).
// Both halves must be published together: if only --app-zoom-inv is set early,
// the terminal counter-zooms by 1/level while <html> is still at the default
// 1.0 → first frame paints at net-scale 1/level (wrong), then snaps to 1.0 when
// the effect runs. Publishing the raw zoom here too keeps them in phase so the
// terminal's net scale is 1.0 from the very first frame. Idempotent with the
// hook effect (same values). Uses applyZoom so the two can never drift apart.
try {
  if (typeof document !== 'undefined') {
    applyZoom(loadZoom());
  }
} catch { /* SSR / no-DOM — hook effect will publish it on mount */ }

function persistZoom(level: number) {
  try {
    localStorage.setItem(STORAGE_KEY, String(Math.round(level * 100) / 100));
  } catch { /* ignore */ }
}

export function useZoom() {
  const [zoom, setZoom] = useState(loadZoom);

  // Apply on mount and whenever zoom changes
  useEffect(() => {
    applyZoom(zoom);
    persistZoom(zoom);
  }, [zoom]);

  const zoomIn = useCallback(() => {
    setZoom(prev => Math.min(MAX_ZOOM, Math.round((prev + STEP) * 100) / 100));
  }, []);

  const zoomOut = useCallback(() => {
    setZoom(prev => Math.max(MIN_ZOOM, Math.round((prev - STEP) * 100) / 100));
  }, []);

  const zoomReset = useCallback(() => {
    setZoom(DEFAULT_ZOOM);
  }, []);

  // Keyboard shortcuts: Cmd+Plus, Cmd+Minus, Cmd+0
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey;
      if (!mod) return;

      // Cmd+= or Cmd+Shift+= (plus key)
      if (e.key === '=' || e.key === '+') {
        e.preventDefault();
        zoomIn();
      }
      // Cmd+- (minus key)
      else if (e.key === '-') {
        e.preventDefault();
        zoomOut();
      }
      // Cmd+0 (reset)
      else if (e.key === '0') {
        e.preventDefault();
        zoomReset();
      }
    };

    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [zoomIn, zoomOut, zoomReset]);

  return { zoom, zoomIn, zoomOut, zoomReset };
}
