/**
 * A Swarm Out wake in the chat (design "the runs this session dispatched, visible in
 * the dispatching tab" r2 §3 ③, invariant 6).
 *
 * Questions: is a user row whose metadata says origin wake shown as one system line
 * naming the runs (not a bubble with the marker text); does every run having come back
 * say so; is a message the person typed a bubble even when it starts with the marker
 * text, and is a wake from before the metadata left as it was.
 */
import { describe, it, expect, beforeAll } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MessageBubble } from '../MessageBubble';
import type { Message } from '../../../../types';

const MARKER = '[Swarm Out auto-continue] Runs you dispatched need you (listed above).';
const user = (text: string, metadata?: Message['metadata']): Message => ({
  id: 'm1', role: 'user', content: [{ type: 'text', text }], timestamp: '2026-11-24T06:00:00Z',
  ...(metadata ? { metadata } : {}),
} as Message);

// the user bubble watches its width (jsdom has no ResizeObserver)
beforeAll(() => {
  globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} } as unknown as typeof ResizeObserver;
});

describe('a wake row', () => {
  it('is a system line naming the runs, not a bubble with the marker', () => {
    render(<MessageBubble message={user(MARKER, { client_id: 'swarm-out-wake-x', origin: 'wake',
      wake: { kind: 'runs', dispatches: [{ id: 'so_1', title: 'H7 Fix login', stage: 'awaiting' }] } })} />);
    const line = screen.getByTestId('wake-line');
    expect(line.textContent).toContain('「H7 Fix login」');
    expect(line.textContent).toContain('came back');
    expect(line.getAttribute('title')).toBe(MARKER);      // what the agent read, on hover
    expect(screen.queryByText(MARKER)).toBeNull();
  });

  it('a run that did not come back is said to have changed', () => {
    render(<MessageBubble message={user(MARKER, { origin: 'wake',
      wake: { kind: 'mixed', dispatches: [{ id: 'so_1', title: 'A', stage: 'awaiting' }, { id: 'so_2', title: 'B', stage: 'stopped' }] } })} />);
    const line = screen.getByTestId('wake-line');
    expect(line.textContent).toContain('「A」');
    expect(line.textContent).toContain('「B」');
    expect(line.textContent).toContain('changed');
  });

  it("a person's message with the marker text stays a bubble", () => {
    render(<MessageBubble message={user(MARKER, { client_id: 'c1' })} />);
    expect(screen.queryByTestId('wake-line')).toBeNull();
    expect(screen.getByText(MARKER)).toBeTruthy();
  });

  it('a row of another origin is not a wake', () => {
    render(<MessageBubble message={user('hello', { origin: 'host' })} />);
    expect(screen.queryByTestId('wake-line')).toBeNull();
  });
});
