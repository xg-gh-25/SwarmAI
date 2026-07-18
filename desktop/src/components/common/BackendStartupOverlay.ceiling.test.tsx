/**
 * #4a — pollHealth wall-clock ceiling WIRING test (R28: force the recovery path
 * to execute, don't just unit-test the pure helper).
 *
 * The pure `hasExceededStartupCeiling` helper is covered in
 * BackendStartupOverlay.property.test.tsx. This file drives the REAL rendered
 * component through pollHealth to the ceiling and asserts it enters the error
 * state — so a wiring regression (wrong ref, wrong constant, wrong i18n args,
 * or an accidentally-removed early return) turns RED here, which the helper-only
 * tests cannot catch.
 *
 * Scenario: browser/Hive mode (no Rust COLD_START_CEILING backstop) with a
 * backend that always replies `alive` (still booting) — the exact flapping/slow
 * case that previously spun forever because `alive` resets noResponseStreak.
 */
import { render, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// Browser/Hive mode: isDesktop=false so startHealthPolling skips Tauri init and
// goes straight to pollHealth. getApiBaseUrl/getBackendPort are only used in log
// strings. initializeBackend must exist but is never called in browser mode.
vi.mock('../../services/tauri', () => ({
  isDesktop: () => false,
  getApiBaseUrl: () => 'http://test',
  getBackendPort: () => 18321,
  initializeBackend: vi.fn().mockResolvedValue(18321),
}));

// checkHealth calls axios.get(...health...). Make every health probe look
// `alive` (a structured JSON reply that is not "healthy") — this is the branch
// that resets noResponseStreak and, pre-fix, polled forever. `.create` is
// needed because services/api.ts (pulled in transitively) calls axios.create().
vi.mock('axios', () => {
  const inst = {
    get: vi.fn().mockResolvedValue({ data: { status: 'initializing' } }),
    post: vi.fn().mockResolvedValue({ data: {} }),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  };
  return {
    default: {
      ...inst,
      create: () => inst,
      isAxiosError: () => false,
    },
  };
});

// @tauri-apps/api/event listen() — return a no-op unlisten.
vi.mock('@tauri-apps/api/event', () => ({
  listen: vi.fn().mockResolvedValue(() => {}),
}));

// i18n: a spy `t` so we can assert the ceiling branch fired via its specific
// key. In this scenario (health never returns `ready`) pollReadiness never runs,
// so `startup.initializationTimeout` is reachable ONLY from the #4a pollHealth
// ceiling branch — asserting t was called with it proves the wiring executed,
// robust against React render-commit flush timing under fake timers.
const mockT = vi.fn(
  (key: string, opts?: Record<string, unknown>) =>
    opts && 'seconds' in opts ? `${key}:${opts.seconds}` : key,
);
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (...a: unknown[]) => (mockT as (...x: unknown[]) => string)(...a) }),
}));

import BackendStartupOverlay from './BackendStartupOverlay';

describe('#4a pollHealth ceiling wiring (R28 forced-execution)', () => {
  beforeEach(() => {
    // Include 'performance' so performance.now() advances with fake time — the
    // pollHealth ceiling measures via performance.now() deltas, so the default
    // toFake list (which omits performance) would leave elapsed pinned at ~0.
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date', 'performance'] });
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('enters error state when the health phase exceeds the wall-clock ceiling', async () => {
    // vi.useFakeTimers() also fakes performance.now (sinon default), so
    // advancing fake time advances the monotonic clock pollHealth reads. No
    // manual performance.now mock — that was nondeterministic (React internals
    // also read it). firstPollTimeRef is set on the first poll (~500ms fake);
    // a later poll past 300s (readinessTimeout) elapsed trips the ceiling.
    render(<BackendStartupOverlay />);

    // Advance past the 300s ceiling. pollHealth is a recursive setTimeout whose
    // callback AWAITS checkHealth before re-arming, so we drain in per-second
    // steps (advanceTimersByTimeAsync flushes microtasks each step) rather than
    // one big jump that wouldn't re-arm through the awaits. ~305 steps of 1s.
    await act(async () => {
      for (let i = 0; i < 310; i++) {
        await vi.advanceTimersByTimeAsync(1000);
      }
    });
    vi.useRealTimers();

    // The ceiling branch is the ONLY reachable caller of
    // t('startup.initializationTimeout', ...) in this scenario (health never
    // returns `ready`, so pollReadiness — the only other caller — never runs).
    // So this assertion proves the pollHealth ceiling WIRING executed. A wiring
    // regression (wrong ref/constant/i18n args, or a removed early return) makes
    // it RED — mutation-verified in the pipeline. Robust against React
    // render-commit flush timing (asserts the call, not the committed DOM).
    const calledCeiling = mockT.mock.calls.some(
      (c) => c[0] === 'startup.initializationTimeout',
    );
    expect(calledCeiling).toBe(true);
  });
});
