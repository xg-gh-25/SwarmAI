/**
 * App-wide zoom control via Cmd+Plus / Cmd+Minus / Cmd+0.
 *
 * Applies CSS `zoom` on `<html>` element — works in Tauri webview (WebKit/Chromium).
 * Persists zoom level to localStorage so it survives app restarts.
 *
 * Zoom range: 50% – 200%, step: 10%.
 */
import { useEffect, useCallback, useState } from 'react';

const STORAGE_KEY = 'swarmai-zoom-level';
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
}

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
