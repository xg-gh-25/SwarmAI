/**
 * Custom DOM event names for Explorer → Chat cross-component communication.
 *
 * These events bridge the workspace explorer (which lives inside ExplorerProvider)
 * with ChatPage (which owns the attachment and input state). Using constants
 * prevents silent breakage from event name typos.
 *
 * Flow: FileContextMenu → VirtualizedTree → window.dispatchEvent → ChatPage listener
 */

/** Attach a workspace file to the active chat tab. */
export const EXPLORER_ATTACH_FILE = 'swarm:attach-file' as const;

/** Attach a file and focus the chat input (Ask Swarm about this). */
export const EXPLORER_ASK_ABOUT_FILE = 'swarm:ask-about-file' as const;

/** Open an integrated terminal cwd'd into a directory (right-click a folder).
 *  detail: { path: string } — the directory path. Listener: ThreeColumnLayout.
 *  Uses the same window-event bridge idiom as the attach/ask events so no prop
 *  needs threading through the 4-layer explorer tree. */
export const EXPLORER_OPEN_TERMINAL = 'swarm:open-terminal-here' as const;
