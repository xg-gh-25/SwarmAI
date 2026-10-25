/**
 * NextSteps.test.tsx — next-step chips add to the input instead of replacing it.
 *
 * What is tested:
 *   - Clicking a chip's text (and the 1/2/3 shortcut) dispatches the inject event
 *     with append: true, so picking a second chip keeps the first in the input
 *     (ChatInput's append handling is covered in ChatInput.test.tsx).
 *   - ↑ still sends that one step now (autoSend, no append).
 * Methodology: render the real component, capture the window inject events.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k }) }));

import { NextSteps } from './NextSteps';
import { INJECT_CHAT_INPUT, type InjectChatInputDetail } from '../injectChatInput';

let injected: InjectChatInputDetail[] = [];
const onInject = (e: Event) => injected.push((e as CustomEvent<InjectChatInputDetail>).detail);

beforeEach(() => { injected = []; window.addEventListener(INJECT_CHAT_INPUT, onInject); });
afterEach(() => { window.removeEventListener(INJECT_CHAT_INPUT, onInject); cleanup(); });

const steps = ['Run the tests', 'Commit this'];

describe('NextSteps chips', () => {
  it('clicking a chip text appends to the input', () => {
    render(<NextSteps steps={steps} />);
    const fills = screen.getAllByTestId('next-step-fill');
    fireEvent.click(fills[0]);
    fireEvent.click(fills[1]);
    expect(injected).toEqual([
      { text: 'Run the tests', focus: true, autoSend: false, append: true },
      { text: 'Commit this', focus: true, autoSend: false, append: true },
    ]);
  });

  it('the number shortcut appends too', () => {
    render(<NextSteps steps={steps} />);
    fireEvent.keyDown(window, { key: '2' });
    expect(injected).toEqual([{ text: 'Commit this', focus: true, autoSend: false, append: true }]);
  });

  it('send-now sends that step alone', () => {
    render(<NextSteps steps={steps} />);
    fireEvent.click(screen.getAllByTestId('next-step-send')[0]);
    expect(injected).toEqual([{ text: 'Run the tests', focus: true, autoSend: true }]);
  });
});
