import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { AlertsPill } from './AlertsPill';

/**
 * Tests for AlertsPill (🔔 Needs You) — 2026-08-08 unified Need You channel.
 * The pill is now a count-only entry point: calm/alert states + click OPENS the
 * fullscreen needs-you overlay via dispatchUiCommand('needs-you'). There is no
 * local popover anymore (the overlay holds the full double-axis queue).
 */
describe('AlertsPill', () => {

  it('CALM: count 0 → pill shows no count badge', () => {
    render(<AlertsPill count={0} />);
    expect(screen.getByText('Needs You')).toBeTruthy();
    expect(screen.queryByText(/^\d+$/)).toBeNull();
  });

  it('ALERT: count N → pill shows the count badge', () => {
    render(<AlertsPill count={2} />);
    expect(screen.getByText('2')).toBeTruthy();
  });

  it('click actually fires swarm:show-needs-you (cmd id RESOLVES, not just called)', () => {
    // Non-vacuous: use the REAL dispatchUiCommand (no mock) and assert the window
    // event actually fires. A mock that only checks the arg string would pass even
    // if the cmd id is not in UI_COMMAND_TABLE (fail-closed → nothing dispatched) —
    // that is exactly the bug the adversarial gate caught (id was 'needs-you', the
    // allowlist key is 'show-needs-you'). This test now goes RED if the id is wrong.
    const fired: string[] = [];
    const listener = (e: Event) => fired.push(e.type);
    window.addEventListener('swarm:show-needs-you', listener);
    try {
      render(<AlertsPill count={3} />);
      fireEvent.click(screen.getByRole('button', { name: /Alerts/i }));
      expect(fired).toContain('swarm:show-needs-you');
    } finally {
      window.removeEventListener('swarm:show-needs-you', listener);
    }
  });

  it('does NOT render a local popover (overlay replaces it)', () => {
    render(<AlertsPill count={3} />);
    fireEvent.click(screen.getByRole('button', { name: /Alerts/i }));
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});
