/**
 * continueInSession — open a chat session in a tab and put a prompt in its
 * composer for the user to send (docs/design/pipeline-v1-human-loop.md: every
 * dashboard action on a pipeline run ends in the session that drives it).
 *
 * The prompt is never auto-sent: the user reviews it and presses Enter. The inject
 * waits two frames so the target tab's composer is mounted before the event fires
 * (same deferral the overlay close paths use).
 *
 * @exports continueInSession
 */
import { chatService } from '../../services/chat';
import { dispatchInjectChatInput } from '../../pages/chat/injectChatInput';

export async function continueInSession(
  sessionId: string,
  prompt: string,
  resumeSession: ((session: unknown) => boolean) | undefined,
): Promise<boolean> {
  if (!resumeSession || !sessionId) return false;
  let session: unknown;
  try {
    session = await chatService.getSession(sessionId);
  } catch {
    return false; // deleted / unknown session: the caller says where to continue instead
  }
  if (!session || !resumeSession(session)) return false;
  requestAnimationFrame(() => requestAnimationFrame(() => {
    dispatchInjectChatInput({ text: prompt, focus: true, autoSend: false });
  }));
  return true;
}
