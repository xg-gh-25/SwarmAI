/**
 * rawFileUrl — the SINGLE source for a Canvas renderer's raw-file URL.
 *
 * The packaged Tauri webview loads from `tauri://localhost` (the bundled asset
 * protocol), NOT the daemon. A BARE-RELATIVE `/api/workspace/file/raw?...` therefore
 * resolves to `tauri://localhost/api/...` and never reaches the backend — pdf.js gets a
 * non-PDF body ("Invalid PDF structure"), <img>/<video>/<audio> get a non-media body.
 * Prefixing `getApiBaseUrl()` makes the URL ABSOLUTE and origin-correct in every mode:
 *   - packaged desktop → `http://localhost:{daemonPort}`
 *   - dev              → `http://localhost:8000`
 *   - hive/browser     → `''` (same-origin; Caddy proxies /api → relative stays correct)
 *
 * This mirrors the fix already in HtmlRenderer.tsx and centralizes it so a future
 * renderer can't reintroduce the bare-relative bug (RP58 single-authority).
 *
 * @param filePath workspace-relative or absolute file path to stream.
 *
 * ⚠️ SCOPE (known gap, NOT closed here): `/raw` rejects any absolute path outside
 * `$HOME` with HTTP 400 (workspace_api.py `_resolve_file_path`), and — unlike
 * `GET /workspace/file`, which threads `allowed_external` via a `session_id` param
 * (workspace_api.py:926) — `/raw` + `/meta` take NO `session_id`. So an EXTERNAL
 * Canvas-surfaced file (outside `$HOME`) is not streamable by these renderers today;
 * closing that needs a backend change (thread `allowed_external` into `/raw`+`/meta`),
 * tracked separately. The common case — files under `~` (incl. `~/.swarm-ai/SwarmWS`
 * and `~/Desktop`) — is fully served, which is what this fix restores.
 */
import { getApiBaseUrl } from '../../../services/tauri';

export function rawFileUrl(filePath: string): string {
  return `${getApiBaseUrl()}/api/workspace/file/raw?path=${encodeURIComponent(filePath)}`;
}
