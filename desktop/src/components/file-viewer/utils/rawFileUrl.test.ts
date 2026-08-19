/**
 * rawFileUrl helper — the SINGLE source for a Canvas renderer's raw-file URL.
 *
 * WHY this exists (bug: packaged Tauri app cannot render PDF/image/video/audio):
 * the packaged webview origin is `tauri://localhost`, NOT the daemon. A BARE-RELATIVE
 * `/api/workspace/file/raw?...` resolves against the asset protocol and never reaches
 * the backend → pdf.js throws "Invalid PDF structure", <img>/<video> get a non-media
 * body. The fix is to build an ABSOLUTE URL via getApiBaseUrl() (exactly what
 * HtmlRenderer already does). This helper centralizes that so a future renderer can't
 * reintroduce the bare-relative bug.
 *
 * Mutation check: revert the helper to return a bare `/api/...` (drop the
 * getApiBaseUrl() prefix) → the "absolute in desktop mode" test goes RED.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

// getApiBaseUrl() is the origin resolver: '' (hive same-origin) / http://localhost:PORT
// (desktop+dev). We drive it per-test to prove the helper is correct in ALL 3 modes.
const getApiBaseUrl = vi.fn<[], string>();
vi.mock('../../../services/tauri', () => ({
  getApiBaseUrl: () => getApiBaseUrl(),
}));

import { rawFileUrl } from './rawFileUrl';

beforeEach(() => {
  getApiBaseUrl.mockReset();
});

describe('rawFileUrl', () => {
  it('packaged desktop: builds an ABSOLUTE daemon URL (getApiBaseUrl-prefixed)', () => {
    getApiBaseUrl.mockReturnValue('http://localhost:18321');
    const url = rawFileUrl('Knowledge/Designs/deck.pdf');
    // The whole point of the fix: absolute, pointing at the daemon origin — NOT a
    // bare-relative path that would resolve against tauri://localhost.
    expect(url).toBe(
      `http://localhost:18321/api/workspace/file/raw?path=${encodeURIComponent('Knowledge/Designs/deck.pdf')}`,
    );
    expect(url.startsWith('http://localhost:18321/')).toBe(true);
  });

  it('dev mode: prefixes the dev backend origin (8000)', () => {
    getApiBaseUrl.mockReturnValue('http://localhost:8000');
    expect(rawFileUrl('a/b.png')).toBe(
      `http://localhost:8000/api/workspace/file/raw?path=${encodeURIComponent('a/b.png')}`,
    );
  });

  it('hive same-origin: empty base → stays relative (correct — Caddy proxies /api)', () => {
    getApiBaseUrl.mockReturnValue('');
    expect(rawFileUrl('report.html')).toBe(
      `/api/workspace/file/raw?path=${encodeURIComponent('report.html')}`,
    );
  });

  it('encodes paths with spaces / CJK / special chars', () => {
    getApiBaseUrl.mockReturnValue('http://localhost:18321');
    const p = 'Knowledge/记录 & notes/文件 (1).pdf';
    const url = rawFileUrl(p);
    expect(url).toBe(`http://localhost:18321/api/workspace/file/raw?path=${encodeURIComponent(p)}`);
    // No raw spaces/ampersands leak into the query.
    expect(url).not.toContain(' ');
    expect(url.split('path=')[1]).not.toContain('&');
  });

  it('carries only the path param (no session_id — /raw does not honor it)', () => {
    // The `/raw` endpoint takes ONLY `path`; there is intentionally no session_id
    // surface here (see rawFileUrl docstring § SCOPE). Guards against a future
    // re-introduction of an inert param.
    getApiBaseUrl.mockReturnValue('http://localhost:18321');
    const url = rawFileUrl('deck.pdf');
    expect(url).toBe(
      `http://localhost:18321/api/workspace/file/raw?path=${encodeURIComponent('deck.pdf')}`,
    );
    expect(url).not.toContain('session_id');
  });
});
