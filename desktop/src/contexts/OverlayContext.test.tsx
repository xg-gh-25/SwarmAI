/**
 * OverlayContext tests — the single-overlay invariant + the two-way hybrid bridge
 * (Gate-1 WARN5, run_fdeaead8). These lock the M1 contract: at most one fullscreen
 * surface open across the legacy (useExclusiveOverlay) ↔ new (this context) systems.
 *
 * Methodology: drive the real provider via its hook (no mocks — the bridge IS the
 * behavior under test), assert `activeOverlay` transitions on real window events.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { render, screen, act, cleanup } from '@testing-library/react';
import { OverlayProvider, useOverlay } from './OverlayContext';
import { BACK_TO_CHAT_EVENT, ALL_SHOW_EVENTS } from '../components/layout/useExclusiveOverlay';

function Probe() {
  const { activeOverlay, openOverlay, closeOverlay } = useOverlay();
  return (
    <div>
      <span data-testid="active">{activeOverlay ?? 'null'}</span>
      <button data-testid="open-todo" onClick={() => openOverlay('todo')}>open todo</button>
      <button data-testid="open-jobs" onClick={() => openOverlay('jobs')}>open jobs</button>
      <button data-testid="close" onClick={() => closeOverlay()}>close</button>
    </div>
  );
}

function renderProbe() {
  return render(<OverlayProvider><Probe /></OverlayProvider>);
}

const active = () => screen.getByTestId('active').textContent;

afterEach(cleanup);

describe('OverlayContext — single-overlay state', () => {
  it('starts with no overlay open', () => {
    renderProbe();
    expect(active()).toBe('null');
  });

  it('openOverlay sets the active id; closeOverlay clears it', () => {
    renderProbe();
    act(() => screen.getByTestId('open-todo').click());
    expect(active()).toBe('todo');
    act(() => screen.getByTestId('close').click());
    expect(active()).toBe('null');
  });

  it('opening a second new-host overlay replaces the first (single slot)', () => {
    renderProbe();
    act(() => screen.getByTestId('open-todo').click());
    expect(active()).toBe('todo');
    act(() => screen.getByTestId('open-jobs').click());
    expect(active()).toBe('jobs');
  });
});

describe('OverlayContext — two-way hybrid bridge (mutual exclusion with legacy)', () => {
  it('AFFERENT: a legacy show-event closes the new-host overlay', () => {
    renderProbe();
    act(() => screen.getByTestId('open-todo').click());
    expect(active()).toBe('todo');
    // A legacy overlay opens via the old bus → new-host overlay must close.
    act(() => window.dispatchEvent(new CustomEvent(ALL_SHOW_EVENTS[0])));
    expect(active()).toBe('null');
  });

  it('AFFERENT: an EXTERNAL back-to-chat broadcast closes the new-host overlay', () => {
    renderProbe();
    act(() => screen.getByTestId('open-todo').click());
    expect(active()).toBe('todo');
    // The Chat hero (or any legacy close) broadcasts back-to-chat → new-host closes.
    act(() => window.dispatchEvent(new CustomEvent(BACK_TO_CHAT_EVENT)));
    expect(active()).toBe('null');
  });

  it('EFFERENT: opening a new-host overlay broadcasts back-to-chat (closes legacy) WITHOUT closing itself', () => {
    // The self-broadcast guard: our own open() fires BACK_TO_CHAT to close legacy
    // overlays, but the afferent listener must NOT null the overlay we just opened.
    let backToChatSeen = 0;
    const spy = () => { backToChatSeen += 1; };
    window.addEventListener(BACK_TO_CHAT_EVENT, spy);
    try {
      renderProbe();
      act(() => screen.getByTestId('open-todo').click());
      // legacy-closing broadcast WAS fired…
      expect(backToChatSeen).toBe(1);
      // …but the overlay we opened is still open (self-broadcast did not null it).
      expect(active()).toBe('todo');
    } finally {
      window.removeEventListener(BACK_TO_CHAT_EVENT, spy);
    }
  });
});
