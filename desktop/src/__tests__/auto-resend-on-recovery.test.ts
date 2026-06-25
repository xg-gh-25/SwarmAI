/**
 * auto-resend-on-recovery.test.ts
 *
 * Locks the "swallowed question" fix (option A): a CONNECTION-PHASE send that
 * exhausts its ~7s reconnect budget while the backend is down (e.g. a ~60s
 * daemon redeploy) is re-sent automatically when health flips back.
 *
 * Models two production transitions (mirrors the real logic in
 * useChatStreamingLifecycle.ts onError terminal branch + ChatPage
 * resendTabOnRecovery), since the handlers themselves are not unit-isolatable:
 *  - armResend(): when the error handler arms _pendingResendOnRecovery
 *  - resendOnRecovery(): what the backend-recovered handler does per tab
 *
 * Key invariants under test:
 *  - Arm ONLY on connection-phase (!hadData); never mid-stream.
 *  - Bounded by RESEND_MAX_ATTEMPTS (flapping backend can't loop forever).
 *  - Idempotent: a duplicate backend-recovered can't double-send (flag cleared
 *    atomically before resend).
 *  - A manual send supersedes a pending resend.
 *  - userStopped streams are never auto-resent.
 */
import { describe, it, expect } from 'vitest';

const RESEND_MAX_ATTEMPTS = 2;

interface TabModel {
  hasReceivedData: boolean;
  userStopped: boolean;
  hasRetryFn: boolean;
  isStreaming: boolean;
  _pendingResendOnRecovery: boolean;
  _pendingResendAttempts: number;
}

function makeTab(overrides: Partial<TabModel> = {}): TabModel {
  return {
    hasReceivedData: false,
    userStopped: false,
    hasRetryFn: true,
    isStreaming: false,
    _pendingResendOnRecovery: false,
    _pendingResendAttempts: 0,
    ...overrides,
  };
}

/** Mirrors the onError terminal-branch arming gate. */
function armResend(tab: TabModel): TabModel {
  const canArm =
    !tab.hasReceivedData &&
    tab.hasRetryFn &&
    !tab.userStopped &&
    tab._pendingResendAttempts < RESEND_MAX_ATTEMPTS;
  return canArm ? { ...tab, _pendingResendOnRecovery: true } : { ...tab };
}

/** Mirrors ChatPage.resendTabOnRecovery; returns [didResend, nextTab]. */
function resendOnRecovery(tab: TabModel): [boolean, TabModel] {
  if (!tab._pendingResendOnRecovery || !tab.hasRetryFn || tab.isStreaming) {
    return [false, { ...tab }];
  }
  // Atomic clear + bump BEFORE resend (double-fire guard), then start streaming.
  return [
    true,
    {
      ...tab,
      _pendingResendOnRecovery: false,
      _pendingResendAttempts: tab._pendingResendAttempts + 1,
      isStreaming: true,
    },
  ];
}

/** Mirrors handleSendMessage clearing the flags on a manual send. */
function manualSend(tab: TabModel): TabModel {
  return { ...tab, _pendingResendOnRecovery: false, _pendingResendAttempts: 0, isStreaming: true };
}

describe('Auto-resend arming (onError terminal branch)', () => {
  it('connection-phase exhaustion arms resend', () => {
    const tab = armResend(makeTab({ hasReceivedData: false }));
    expect(tab._pendingResendOnRecovery).toBe(true);
  });

  it('mid-stream failure (hasReceivedData) does NOT arm — would double-answer', () => {
    const tab = armResend(makeTab({ hasReceivedData: true }));
    expect(tab._pendingResendOnRecovery).toBe(false);
  });

  it('user-stopped stream is never auto-resent', () => {
    const tab = armResend(makeTab({ userStopped: true }));
    expect(tab._pendingResendOnRecovery).toBe(false);
  });

  it('no retryStreamFn → cannot arm', () => {
    const tab = armResend(makeTab({ hasRetryFn: false }));
    expect(tab._pendingResendOnRecovery).toBe(false);
  });

  it('attempts at cap → does not re-arm (bounded against backend flapping)', () => {
    const tab = armResend(makeTab({ _pendingResendAttempts: RESEND_MAX_ATTEMPTS }));
    expect(tab._pendingResendOnRecovery).toBe(false);
  });
});

describe('Auto-resend on backend-recovered', () => {
  it('armed + idle + retryFn → resends, clears flag, bumps attempt, starts streaming', () => {
    const [did, next] = resendOnRecovery(armResend(makeTab()));
    expect(did).toBe(true);
    expect(next._pendingResendOnRecovery).toBe(false);
    expect(next._pendingResendAttempts).toBe(1);
    expect(next.isStreaming).toBe(true);
  });

  it('idempotent: a duplicate backend-recovered cannot double-send', () => {
    const [did1, after1] = resendOnRecovery(armResend(makeTab()));
    expect(did1).toBe(true);
    const [did2, after2] = resendOnRecovery(after1);
    expect(did2).toBe(false);
    expect(after2._pendingResendAttempts).toBe(1); // not incremented twice
  });

  it('not armed → no resend (falls through to mergeTabFromDb in production)', () => {
    const [did] = resendOnRecovery(makeTab({ _pendingResendOnRecovery: false }));
    expect(did).toBe(false);
  });

  it('already streaming → no resend (never interrupt a live turn)', () => {
    const [did] = resendOnRecovery(makeTab({ _pendingResendOnRecovery: true, isStreaming: true }));
    expect(did).toBe(false);
  });

  it('bounded loop: a flapping backend resends at most RESEND_MAX_ATTEMPTS times', () => {
    let tab = makeTab();
    let resends = 0;
    // Simulate up to 5 down→up flaps; each "up" arms (if it failed again) then resends.
    for (let i = 0; i < 5; i++) {
      tab = armResend({ ...tab, isStreaming: false, hasReceivedData: false });
      const [did, next] = resendOnRecovery(tab);
      if (did) resends++;
      tab = next;
    }
    expect(resends).toBe(RESEND_MAX_ATTEMPTS);
  });
});

describe('Manual send supersedes pending resend', () => {
  it('clears the armed flag and resets the attempt counter', () => {
    const armed = armResend(makeTab());
    expect(armed._pendingResendOnRecovery).toBe(true);
    const afterManual = manualSend(armed);
    expect(afterManual._pendingResendOnRecovery).toBe(false);
    expect(afterManual._pendingResendAttempts).toBe(0);
    // A backend-recovered now must NOT resend.
    const [did] = resendOnRecovery({ ...afterManual, isStreaming: false });
    expect(did).toBe(false);
  });
});
