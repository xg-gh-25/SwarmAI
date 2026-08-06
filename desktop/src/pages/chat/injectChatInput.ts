/**
 * injectChatInput — the ONE typed contract for the `swarm:inject-chat-input`
 * window event that lands a prompt string into the active chat tab's input box.
 *
 * Before this module the event was an untyped `CustomEvent` whose
 * `{ text, focus?, autoSend? }` detail shape was re-declared inline at every
 * dispatcher (ChatPage's two dispatch handlers + FileEditorCore's review-feedback
 * path) and at the listener (ChatInput). A field rename or a new field would drift
 * silently across those 4 sites — the ACT/SENSE-contract bug class the OverlayHost
 * re-architecture named. This module makes the event name + payload a single
 * exported contract that all 4 sites import.
 *
 * The const value MUST stay byte-identical (`swarm:inject-chat-input`) — external
 * listeners key on the literal string.
 *
 * @exports INJECT_CHAT_INPUT, InjectChatInputDetail, dispatchInjectChatInput
 */

/** The window event name. Byte-identical to the historical literal — do not rename. */
export const INJECT_CHAT_INPUT = 'swarm:inject-chat-input';

/** The event payload. `text` is required; `focus`/`autoSend` default to falsy. */
export interface InjectChatInputDetail {
  /** The prompt/text to drop into the active tab's input box. */
  text: string;
  /** Move keyboard focus to the textarea after injecting (default false). */
  focus?: boolean;
  /** Immediately send the message instead of waiting for the user (default false).
   *  Overlay dispatch keeps this false (user reviews + sends); the file-review
   *  feedback path sets it true. */
  autoSend?: boolean;
}

/** Dispatch the typed inject event. The single sanctioned way to fire it — callers
 *  never hand-build the CustomEvent, so the name + shape can't drift. */
export function dispatchInjectChatInput(detail: InjectChatInputDetail): void {
  window.dispatchEvent(new CustomEvent<InjectChatInputDetail>(INJECT_CHAT_INPUT, { detail }));
}
