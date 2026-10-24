/**
 * injectChatInput.test.ts — injectDispatchedPrompt, the one place that decides
 * whether an overlay-dispatched prompt is only filled or also sent.
 *
 * Tested: default and `autoSend: false` fill (focus, no send); `autoSend: true`
 * sends; nothing but an explicit true sends. Methodology: listen to the real
 * window event and assert its payload.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { INJECT_CHAT_INPUT, injectDispatchedPrompt, type InjectChatInputDetail } from './injectChatInput';

let got: InjectChatInputDetail[] = [];
const on = (e: Event) => got.push((e as CustomEvent<InjectChatInputDetail>).detail);
beforeEach(() => { got = []; window.addEventListener(INJECT_CHAT_INPUT, on); });
afterEach(() => window.removeEventListener(INJECT_CHAT_INPUT, on));

describe('injectDispatchedPrompt', () => {
  it('fills by default, focused, not sent', () => {
    injectDispatchedPrompt('p');
    injectDispatchedPrompt('q', { autoSend: false });
    expect(got).toEqual([
      { text: 'p', focus: true, autoSend: false },
      { text: 'q', focus: true, autoSend: false },
    ]);
  });

  it('sends only on an explicit autoSend: true', () => {
    injectDispatchedPrompt('p', { autoSend: true });
    expect(got).toEqual([{ text: 'p', focus: true, autoSend: true }]);
  });
});
