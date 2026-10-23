/**
 * uiContext — the agent-proprioception (SENSE) payload + its wire serializer.
 *
 * A request-time snapshot of the agent's own UI state (open file + Canvas state +
 * which nav overlay is open), sent with each chat request so the agent can
 * perceive what it is currently showing the user. Superset of the legacy
 * "currently open file" descriptor — the wire field stays `editor_context` and a
 * file-only snapshot still serializes to the exact legacy 2-field shape.
 *
 * The Canvas part follows docs canvas-proprioception.md §3.1: every field is what
 * the user sees on THAT tab right now (visible / tab / list / pin / file / pending
 * open / output rows / the last turn's Canvas commands). Sizes are capped here
 * (MAX_SNAPSHOT_OUTPUTS, MAX_SNAPSHOT_COMMANDS) so the payload stays small.
 *
 * This module is the SINGLE serialization source (Gate-1 CRITICAL): the send path
 * MUST call `toEditorContextPayload` — never hand-pick fields inline — or new
 * fields get silently dropped at the wire boundary.
 */

/** Output rows sent per snapshot (newest first); the total goes as outputCount. */
export const MAX_SNAPSHOT_OUTPUTS = 8;
/** Canvas commands remembered per tab between two sends. */
export const MAX_SNAPSHOT_COMMANDS = 3;

/** hidden = no Canvas; rail = folded to the thin side rail; open = shown. */
export type CanvasVisible = 'hidden' | 'rail' | 'open';
/** How the shown text file is displayed (FileEditorCore). Absent for renderers. */
export type CanvasFileView = 'preview' | 'edit' | 'changes';

/** How the shown file is displayed, as FileEditorCore reports it (§3.2). */
export interface CanvasFileViewReport {
  path: string;
  view: CanvasFileView;
  review: boolean;
}

export interface CanvasFileSnapshot {
  path: string;
  name: string;
  /** canvas = Canvas opened it by itself (auto-surface / default selection). */
  openedBy: 'user' | 'canvas';
  dirty: boolean;
  view?: CanvasFileView;
  review?: boolean;
}

export interface CanvasOutputRow {
  path: string;
  kind?: string;
  /** Not seen by the user yet (listed after they last had this Canvas open). */
  isNew: boolean;
}

/** What happened to one agent ui_command on the Canvas (§5). */
export interface UiCommandResult {
  cmd: string;
  result: 'applied' | 'ignored' | 'pending';
  reason?: string;
}

/** Canvas (output panel) UI state — frontend camelCase mirror of backend CanvasState. */
export interface CanvasSnapshot {
  /** visible !== 'hidden' (kept for the legacy renderer / old fields). */
  open: boolean;
  visible: CanvasVisible;
  tab: 'artifacts' | 'changes';
  listFolded: boolean;
  pinned: boolean;
  file: CanvasFileSnapshot | null;
  /** Path of an open waiting on "discard edits and open / keep editing". */
  pendingOpen: string | null;
  outputCount: number;
  outputs: CanvasOutputRow[];
  commands: UiCommandResult[];
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
    visible: CanvasVisible;
    tab: 'artifacts' | 'changes';
    list_folded: boolean;
    pinned: boolean;
    output_count: number;
    outputs: { path: string; kind?: string; new: boolean }[];
    file?: { path: string; name: string; opened_by: 'user' | 'canvas'; dirty: boolean; view?: CanvasFileView; review?: boolean };
    pending_open?: string;
    commands?: { cmd: string; result: UiCommandResult['result']; reason?: string }[];
  };
  active_overlay?: string;
}

/** True when a canvas snapshot carries any reportable (non-default) state. */
function canvasHasState(c: CanvasSnapshot | null | undefined): c is CanvasSnapshot {
  return !!c && (c.open || c.outputCount > 0 || c.pinned || c.commands.length > 0);
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
 * SSOT for the send boundary — includes canvas + active_overlay whenever present.
 * Returns null when there is nothing to report, so the caller omits the field.
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
      visible: c.visible,
      tab: c.tab,
      list_folded: c.listFolded,
      pinned: c.pinned,
      output_count: c.outputCount,
      outputs: c.outputs.slice(0, MAX_SNAPSHOT_OUTPUTS).map((o) => ({ path: o.path, ...(o.kind ? { kind: o.kind } : {}), new: o.isNew })),
      ...(c.file ? {
        file: {
          path: c.file.path,
          name: c.file.name,
          opened_by: c.file.openedBy,
          dirty: c.file.dirty,
          ...(c.file.view ? { view: c.file.view } : {}),
          ...(c.file.review ? { review: true } : {}),
        },
      } : {}),
      ...(c.pendingOpen ? { pending_open: c.pendingOpen } : {}),
      ...(c.commands.length ? { commands: c.commands.slice(-MAX_SNAPSHOT_COMMANDS).map((r) => ({ cmd: r.cmd, result: r.result, ...(r.reason ? { reason: r.reason } : {}) })) } : {}),
    };
  }
  if (snap!.activeOverlay) {
    payload.active_overlay = snap!.activeOverlay;
  }
  return payload;
}
