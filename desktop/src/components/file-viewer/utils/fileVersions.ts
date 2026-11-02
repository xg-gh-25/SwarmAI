/**
 * fileVersions — a read-only "versions" document for the Canvas: one file at two
 * versions (a base and a tip) that is NOT a file on this machine, shown as its diff.
 *
 * The one producer is the Swarm Out run card (RunOverlay): a returned repository's
 * changed file, read from this machine's git objects (backend
 * core/hive_return.file_versions). The text is the Hive's: the Canvas shows it read-only
 * and never saves it, never reads or writes a path for it, never resolves it.
 *
 * The event is a plain in-app document event, deliberately NOT in UI_COMMAND_TABLE
 * (utils/uiCommands): neither the backend's ui_command nor an agent can send it, and the
 * agent's open-canvas-file (swarm:open-file) never carries versions.
 *
 * @exports OPEN_FILE_VERSIONS_EVENT, FileVersionsDetail, openFileVersions, versionsDocPath
 */

/** useCanvasHost listens to it on **document** (like swarm:open-file). */
export const OPEN_FILE_VERSIONS_EVENT = 'swarm:open-file-versions';

export interface FileVersionsDetail {
  /** where the versions come from, e.g. ``hive:<dispatch>@<tip12>``: part of the
   *  document's name, never a path on this machine */
  source: string;
  /** the file's path inside its repository (what the document is named after) */
  path: string;
  /** the text before (null: the file did not exist) */
  base: string | null;
  /** the text after (null: the file was deleted) */
  tip: string | null;
}

/** The document's name in the Canvas: ``<source>/<path>``. Never a workspace path: a
 *  source has a ``:``, and nothing reads or writes it. */
export function versionsDocPath(d: Pick<FileVersionsDetail, 'source' | 'path'>): string {
  return `${d.source}/${d.path}`;
}

/** Open the versions document in the current tab's Canvas. */
export function openFileVersions(detail: FileVersionsDetail): void {
  document.dispatchEvent(new CustomEvent(OPEN_FILE_VERSIONS_EVENT, { detail }));
}
