/**
 * fakeCanvasBackend — an in-memory stand-in for the backend's Canvas record, so hook
 * tests keep driving the REAL production path (swarm:file-changed → the store's
 * debounced refresh → GET /canvas/session/{id}) without a server.
 *
 * It records each `swarm:file-changed` the way core/session_outputs does (rail kinds
 * only; a delete marks the row; a write counts), keyed by the session the store maps
 * the event's tab to — the backend attributes by the session that streamed it. Every
 * other api.get call falls through to the test's own mock implementation.
 *
 * @exports installFakeCanvasBackend, settleCanvas
 */
import { act } from '@testing-library/react';
import type { Mock } from 'vitest';
import { LIVE_REFRESH_MS, sessionForTab, type SessionArtifact } from '../services/canvasSession';

const RAIL = new Set(['content', 'knowledge', 'source-final', 'external-diff', 'external-nodiff']);

export function installFakeCanvasBackend(apiGet: Mock) {
  const rows = new Map<string, Map<string, SessionArtifact>>();
  let tick = 0;
  const onEvent = (e: Event) => {
    const d = (e as CustomEvent<Record<string, string | undefined>>).detail ?? {};
    const sid = sessionForTab(d.tabId);
    const ap = d.absolutePath || d.path;
    if (!sid || !ap) return;
    const m = rows.get(sid) ?? new Map<string, SessionArtifact>();
    rows.set(sid, m);
    const cur = m.get(ap);
    if (d.operation === 'deleted') {
      if (cur) m.set(ap, { ...cur, deleted: true });
      return;
    }
    if (!d.kind || !RAIL.has(d.kind)) return;
    const at = new Date(Date.now() + tick++).toISOString();
    m.set(ap, {
      path: d.path ?? ap, absolutePath: ap, kind: d.kind, role: 'Other', product: false, starred: false,
      displayLabel: (d.path ?? ap).split('/').pop() ?? ap, firstAt: cur?.firstAt ?? at, lastAt: at,
      turns: (cur?.turns ?? 0) + 1, deleted: false, exists: true, ...(d.baseRef ? { baseRef: d.baseRef } : {}),
    });
  };
  // capture phase: recorded before any store listener sees the event (the backend
  // writes before it forwards the event)
  window.addEventListener('swarm:file-changed', onEvent, true);
  const fallback = apiGet.getMockImplementation();
  apiGet.mockImplementation(async (url: string, opts?: unknown) => {
    const m = /^\/canvas\/session\/([^/?]+)/.exec(String(url));
    if (m) {
      const sid = decodeURIComponent(m[1]);
      return { data: { artifacts: [...(rows.get(sid)?.values() ?? [])], changes: [] } };
    }
    return fallback ? fallback(url, opts) : { data: {} };
  });
  return {
    rows,
    uninstall: () => window.removeEventListener('swarm:file-changed', onEvent, true),
  };
}

/** Let the store's debounced live refresh run and its answer land. */
export async function settleCanvas() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, LIVE_REFRESH_MS + 30));
    await new Promise((r) => setTimeout(r, 0));
  });
}
