/**
 * Tests for recoveryToastMessage — the honest recovery-toast selector.
 *
 * Testing methodology: pure-function unit tests (vitest).
 *
 * What is tested:
 *   The mapping from a recovery "kind" (set by the Tauri watchdog events)
 *   to the user-facing success toast, after the boot_id-aware
 *   restarted-vs-resumed distinction was added to useHealthMonitor.
 *
 * Key invariants:
 *   - "restarted" (boot_id changed → new process) is the ONLY kind that may
 *     claim a restart. It must never be shown for a resume or unknown.
 *   - "resumed" (same process, transient stall) must NOT claim a restart.
 *   - null (JS-poller-only recovery, no Tauri event) → generic reconnect,
 *     never an overclaim in either direction.
 *   - The "crashed" wording is fully retired from the recovery path.
 */

import { describe, it, expect } from 'vitest';
import { recoveryToastMessage } from '../useHealthMonitor';

describe('recoveryToastMessage', () => {
  it('restarted → claims a restart (only this kind may)', () => {
    expect(recoveryToastMessage('restarted')).toBe('Backend restarted and reconnected');
  });

  it('resumed → "responding again", never claims a restart', () => {
    const msg = recoveryToastMessage('resumed');
    expect(msg).toBe('Backend responding again');
    expect(msg.toLowerCase()).not.toContain('restart');
  });

  it('null (unknown / JS-poller recovery) → generic reconnect, no restart claim', () => {
    const msg = recoveryToastMessage(null);
    expect(msg).toBe('Backend reconnected');
    expect(msg.toLowerCase()).not.toContain('restart');
  });

  it('never emits the retired "crashed" wording for any kind', () => {
    for (const kind of ['restarted', 'resumed', null] as const) {
      expect(recoveryToastMessage(kind).toLowerCase()).not.toContain('crash');
    }
  });

  it('only "restarted" is allowed to mention a restart', () => {
    const mentionsRestart = (['restarted', 'resumed', null] as const).filter((k) =>
      recoveryToastMessage(k).toLowerCase().includes('restart'),
    );
    expect(mentionsRestart).toEqual(['restarted']);
  });
});
