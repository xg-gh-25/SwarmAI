/**
 * uiContext — the agent-proprioception (SENSE) payload + its wire serializer.
 *
 * A request-time snapshot of the agent's own UI state (open file + Canvas state +
 * which nav overlay is open), sent with each chat request so the agent can
 * perceive what it is currently showing the user. Superset of the legacy
 * "currently open file" descriptor — the wire field stays `editor_context` and a
 * file-only snapshot still serializes to the exact legacy 2-field shape.
 *
 * This module is the SINGLE serialization source (Gate-1 CRITICAL): the send path
 * MUST call `toEditorContextPayload` — never hand-pick fields inline — or new
 * fields get silently dropped at the wire boundary.
 */

/** Canvas (output panel) UI state — frontend camelCase mirror of backend CanvasState. */
export interface CanvasSnapshot {
  open: boolean;
  outputCount: number;
  pinned: boolean;
  muted: boolean;
  collapsed: boolean;
}

/** The full UI snapshot the agent perceives. All parts optional. */
export interface UiContextSnapshot {
  filePath?: string;
  fileName?: string;
  /** Canvas state, or null when Canvas is closed/unmounted (no stale count). */
  canvas?: CanvasSnapshot | null;
  /** `swarm:show-*` event id of the open fullscreen overlay, or null. */
  activeOverlay?: string | null;
}

/** The snake_case wire payload sent as `editor_context` (superset, backward-compat). */
export interface EditorContextPayload {
  file_path: string;
  file_name: string;
  canvas?: {
    open: boolean;
    output_count: number;
    pinned: boolean;
    muted: boolean;
    collapsed: boolean;
  };
  active_overlay?: string;
}

/** True when a canvas snapshot carries any reportable (non-default) state. */
function canvasHasState(c: CanvasSnapshot | null | undefined): c is CanvasSnapshot {
  return !!c && (c.open || c.outputCount > 0 || c.pinned || c.muted || c.collapsed);
}

/**
 * Is there anything worth sending? Returns false for an empty/degenerate snapshot
 * so the caller can omit `editor_context` entirely (matches legacy null behavior).
 */
export function hasUiState(snap: UiContextSnapshot | null | undefined): boolean {
  if (!snap) return false;
  return !!snap.filePath || canvasHasState(snap.canvas) || !!snap.activeOverlay;
}

/**
 * Serialize a UI snapshot to the `editor_context` wire payload.
 *
 * SSOT for the send boundary — includes canvas + active_overlay whenever present
 * (the Gate-1 CRITICAL fix: the old inline literal copied only file_path/file_name
 * and would silently drop the new fields). Returns null when there is nothing to
 * report, so the caller omits the field (legacy behavior for an empty snapshot).
 */
export function toEditorContextPayload(
  snap: UiContextSnapshot | null | undefined,
): EditorContextPayload | null {
  if (!hasUiState(snap)) return null;
  const payload: EditorContextPayload = {
    file_path: snap!.filePath ?? '',
    file_name: snap!.fileName ?? '',
  };
  if (canvasHasState(snap!.canvas)) {
    const c = snap!.canvas;
    payload.canvas = {
      open: c.open,
      output_count: c.outputCount,
      pinned: c.pinned,
      muted: c.muted,
      collapsed: c.collapsed,
    };
  }
  if (snap!.activeOverlay) {
    payload.active_overlay = snap!.activeOverlay;
  }
  return payload;
}
