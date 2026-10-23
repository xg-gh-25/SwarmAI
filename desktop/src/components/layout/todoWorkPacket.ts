/**
 * todoWorkPacket — a todo's work packet (its `linked_context` JSON) and the guarded
 * parser for it.
 *
 * Kept apart from ToDoOverlay so ChatPage's dispatch (buildDispatchPrompt) can read
 * a work packet without statically importing the overlay, which would pull the whole
 * overlay into the first bundle (docs/design/left-nav-overlay-finish.md §2).
 *
 * Key exports: `WorkPacket`, `parseWorkPacket(linkedContext)`.
 */

/** The work packet a todo carries in `linked_context`. Unknown keys are kept. */
export interface WorkPacket {
  next_step?: string;
  acceptance?: string;
  files?: string[];
  design_docs?: string[];
  commits?: string[];
  sessions?: string[];
  memory_refs?: string[];
  blockers?: string[];
  notes?: string;
  [k: string]: unknown;
}

/** Parse a todo's linked_context JSON string into a work-packet object.
 *  Guarded: null / empty / malformed / non-object → null. */
export function parseWorkPacket(linkedContext: string | null): WorkPacket | null {
  if (!linkedContext) return null;
  try {
    const p = JSON.parse(linkedContext);
    if (typeof p === 'object' && p !== null && !Array.isArray(p)) return p as WorkPacket;
    return null;
  } catch {
    return null;
  }
}
