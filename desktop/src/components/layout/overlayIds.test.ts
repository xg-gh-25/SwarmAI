/**
 * overlayIds tests — lock the overlay-id SSOT invariants (run_06c49540).
 *
 * These are the structural guards that make the OverlayId union the real single
 * source of truth rather than a 4th hand-synced list:
 *
 *   1. registeredOverlayIds() === OVERLAY_IDS  (BOTH directions) — the tuple cannot
 *      drift from the actually-registered surfaces. A tuple entry with no
 *      registerOverlay call (or a registration whose id is not in the tuple) FAILS.
 *      (The type system already blocks the "registered id not in tuple" case at
 *      compile time via spec.id: OverlayId; this test catches the reverse — a tuple
 *      id that no surface registers — which the type system cannot see.)
 *   2. every ALL_SHOW_EVENTS suffix ∈ OVERLAY_IDS — the agent-openable events are all
 *      real registered surfaces (the events⊆ids seam; without it a swarm:show-<x>
 *      with no registered <x> would set a null-rendering activeOverlay).
 *   3. isOverlayId behaves as a correct type guard.
 *
 * Mutation-verified (run_06c49540): removing a registerOverlay call in
 * overlaySurfaces makes test 1 RED; adding a swarm:show-<bogus> to ALL_SHOW_EVENTS
 * makes test 2 RED.
 */
import { describe, it, expect } from 'vitest';
import { OVERLAY_IDS, isOverlayId } from './overlayIds';
import { registeredOverlayIds } from './overlayRegistry';
import { ALL_SHOW_EVENTS } from './useExclusiveOverlay';
import './overlaySurfaces'; // side-effect: register the REAL surfaces with the registry

const SHOW_PREFIX = 'swarm:show-';

describe('overlayIds SSOT invariants', () => {
  it('OVERLAY_IDS exactly equals the actually-registered surface ids (both directions)', () => {
    const registered = registeredOverlayIds();
    // Set-equality both ways: no tuple id is unregistered, no registered id is
    // missing from the tuple. This is what makes the tuple the SSOT, not a copy.
    expect(new Set(registered)).toEqual(new Set(OVERLAY_IDS));
    // And no accidental duplicate registration / tuple entry.
    expect(registered.length).toBe(OVERLAY_IDS.length);
    expect(new Set(OVERLAY_IDS).size).toBe(OVERLAY_IDS.length);
  });

  it('every ALL_SHOW_EVENTS suffix is a registered OverlayId (events ⊆ ids)', () => {
    const suffixes = ALL_SHOW_EVENTS.map((e) => e.slice(SHOW_PREFIX.length));
    const ids = new Set<string>(OVERLAY_IDS);
    for (const suffix of suffixes) {
      expect(ids.has(suffix)).toBe(true);
    }
    // ALL_SHOW_EVENTS is the agent-openable SUBSET — strictly fewer than all ids
    // (library/settings/eval are deliberately not agent-openable). Guard that the
    // subset relationship holds (a regression that made them equal would mean a
    // non-agent surface leaked into the agent ACT vocabulary).
    expect(suffixes.length).toBeLessThan(OVERLAY_IDS.length);
  });

  it('isOverlayId narrows a real id and rejects a non-id', () => {
    expect(isOverlayId('todo')).toBe(true);
    expect(isOverlayId('eval')).toBe(true);
    expect(isOverlayId('histroy')).toBe(false); // typo
    expect(isOverlayId('')).toBe(false);
    expect(isOverlayId('swarm:show-todo')).toBe(false); // the event, not the id
  });
});
