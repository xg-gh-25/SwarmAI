/**
 * OverlayContext tests — the single-overlay invariant + the show-event bridge
 * (run_fdeaead8, M4). These lock the contract: at most one fullscreen surface open,
 * and `swarm:show-<id>` (nav card OR agent ui_action) OPENS the mapped surface —
 * this context is the sole opener now every ALL_SHOW_EVENTS surface is registered.
 *
 * Methodology: drive the real provider via its hook (no mocks — the bridge IS the
 * behavior under test), assert `activeOverlay` transitions on real window events.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { render, screen, act, cleanup } from '@testing-library/react';
import { OverlayProvider, useOverlay } from './OverlayContext';
import { BACK_TO_CHAT_EVENT, ALL_SHOW_EVENTS } from '../components/layout/useExclusiveOverlay';

function Probe() {
  const { activeOverlay, openOverlay, closeOverlay, agentId, setAgentId } = useOverlay();
  return (
    <div>
      <span data-testid="active">{activeOverlay ?? 'null'}</span>
      <span data-testid="agent">{agentId ?? 'null'}</span>
      <button data-testid="open-todo" onClick={() => openOverlay('todo')}>open todo</button>
      <button data-testid="open-jobs" onClick={() => openOverlay('jobs')}>open jobs</button>
      <button data-testid="close" onClick={() => closeOverlay()}>close</button>
      <button data-testid="set-agent-a" onClick={() => setAgentId('agent-a')}>a</button>
      <button data-testid="set-agent-b" onClick={() => setAgentId('agent-b')}>b</button>
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

describe('OverlayContext — show-event bridge + mutual exclusion', () => {
  it('AFFERENT OPEN: a swarm:show-<id> event opens the mapped surface (agent ui_action path)', () => {
    renderProbe();
    // ALL_SHOW_EVENTS[0] is 'swarm:show-swarmws' → id 'swarmws'. This is exactly the
    // event the agent's ui_action (or a nav card) dispatches to open a surface.
    act(() => window.dispatchEvent(new CustomEvent(ALL_SHOW_EVENTS[0])));
    expect(active()).toBe(ALL_SHOW_EVENTS[0].slice('swarm:show-'.length));
  });

  it('AFFERENT OPEN: a second show-event replaces the first (single slot preserved)', () => {
    renderProbe();
    act(() => screen.getByTestId('open-todo').click());
    expect(active()).toBe('todo');
    act(() => window.dispatchEvent(new CustomEvent('swarm:show-jobs')));
    expect(active()).toBe('jobs');
  });

  it('AFFERENT: an EXTERNAL back-to-chat broadcast closes the new-host overlay', () => {
    renderProbe();
    act(() => screen.getByTestId('open-todo').click());
    expect(active()).toBe('todo');
    // The Chat hero (or any legacy close) broadcasts back-to-chat → new-host closes.
    act(() => window.dispatchEvent(new CustomEvent(BACK_TO_CHAT_EVENT)));
    expect(active()).toBe('null');
  });

  it('REACTIVE agentId: setAgentId updates the value consumers see, even while an overlay is OPEN (G3 anti-staleness, run_06c49540)', () => {
    // The bug this guards (Gate-2 MED): agentId used to ride the non-reactive module
    // _bridge, so an open History overlay kept a stale agentId when the agent switched
    // (e.g. the delete-agent fallback effect). Reactive context = consumers re-render.
    renderProbe();
    expect(screen.getByTestId('agent').textContent).toBe('null');
    act(() => screen.getByTestId('set-agent-a').click());
    expect(screen.getByTestId('agent').textContent).toBe('agent-a');
    // Open an overlay, THEN switch agent — the consumer must observe the new value.
    act(() => screen.getByTestId('open-todo').click());
    expect(active()).toBe('todo');
    act(() => screen.getByTestId('set-agent-b').click());
    expect(screen.getByTestId('agent').textContent).toBe('agent-b'); // NOT stale 'agent-a'
    expect(active()).toBe('todo'); // still open — agent switch does not close it
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
