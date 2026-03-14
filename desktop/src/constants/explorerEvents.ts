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
