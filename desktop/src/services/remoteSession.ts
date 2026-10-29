/**
 * Which chat session is a Hive session, and how its HTTP calls reach it
 * (docs/design/2026-10-29-swarm-out-hive-session-tab.md §5).
 *
 * A Hive session opened in the app's Hive tab is not served by this machine's
 * daemon: its calls go to the Host proxy `/api/swarm-out/remote/<sid>/<chat path>`
 * (backend routers/swarm_out_remote.py), which forwards them to the session's Hive.
 * Its live events still come through the one local `/api/chat/events` connection,
 * republished by the Host relay (startRelay / stopRelay).
 *
 * At most one session is remote at a time (there is one Hive tab). For any other
 * session every helper here gives back what the caller had: a service method calls
 * exactly what it called before (design I1).
 *
 * The proxy refuses a write (POST / DELETE) without `Content-Type: application/json`
 * and `X-Swarm-User-Action`, so a remote write sends both and a JSON body, `{}` when
 * the local call sends none (`remoteWrite`, `REMOTE_WRITE_HEADERS`).
 *
 * Exports: setRemote, isRemote, remotePath, remoteWrite, REMOTE_WRITE_HEADERS,
 * relayClientId, startRelay, stopRelay.
 */
import api from './api';

/** The ids the proxy accepts in a path (backend core/swarm_out_remote.ID_RE). */
const ID_RE = /^[A-Za-z0-9_-]{1,128}$/;

let remoteSid: string | null = null;

/** Bind the Hive tab's session (or clear it with null). An id the proxy would not
 *  accept registers nothing: no URL is ever built from it. */
export function setRemote(sid: string | null): void {
  remoteSid = typeof sid === 'string' && ID_RE.test(sid) ? sid : null;
}

/** Whether `sid` is the Hive tab's session. */
export function isRemote(sid: string | null | undefined): boolean {
  return remoteSid !== null && sid === remoteSid;
}

/** `path` (an api-client path such as '/chat/stream') for the remote session's
 *  proxy; any other session's path unchanged. */
export function remotePath(sid: string | null | undefined, path: string): string {
  return isRemote(sid) ? `/swarm-out/remote/${remoteSid}${path}` : path;
}

/** The headers every remote write carries. */
export const REMOTE_WRITE_HEADERS: Readonly<Record<string, string>> = Object.freeze({
  'Content-Type': 'application/json',
  'X-Swarm-User-Action': '1',
});

/** The axios config of a write to the remote session; null for any other session
 *  (the caller then makes its old call unchanged). */
export function remoteWrite(sid: string | null | undefined): { headers: Record<string, string> } | null {
  return isRemote(sid) ? { headers: { ...REMOTE_WRITE_HEADERS } } : null;
}

/** This window's id for the relay's per-client hold: the same for every call from one
 *  window, so two windows never release each other's relay. */
const RELAY_CLIENT_ID = `w-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;

export function relayClientId(): string {
  return RELAY_CLIENT_ID;
}

function relayUrl(sid: string): string {
  if (!ID_RE.test(sid)) throw new Error('invalid session id');
  return `/swarm-out/remote/${sid}/relay`;
}

/** Start (or join) the Host relay of a Hive session; answers the session's state
 *  entry as the streaming-state poll carries it (snake_case: convert it with
 *  chatService.toStreamingStateEntry). */
export async function startRelay(sid: string): Promise<Record<string, unknown> | null> {
  const r = await api.post<{ session_id: string; state: Record<string, unknown> | null }>(
    relayUrl(sid), { client_id: RELAY_CLIENT_ID }, { headers: { ...REMOTE_WRITE_HEADERS } });
  return r.data?.state ?? null;
}

/** Drop this window's hold on the relay (the Host stops it when no window holds it). */
export async function stopRelay(sid: string): Promise<void> {
  await api.delete(relayUrl(sid), { data: { client_id: RELAY_CLIENT_ID }, headers: { ...REMOTE_WRITE_HEADERS } });
}
