/**
 * restoreBackup() SSE stall-guard tests.
 *
 * Bug (run_da5da0b1): the restore SSE read loop had no client-side stall-guard.
 * A backend stream that stalls silently (yields no further event, never closes)
 * left `reader.read()` awaiting forever — the onboarding wizard's StepRestore
 * showed only a progress bar with no escape (deadlock).
 *
 * These tests drive the REAL restoreBackup async generator against a fake fetch
 * whose reader stalls after the first chunk, and assert the idle stall-guard
 * emits a terminal `.error` event (the field StepRestore keys on) + aborts the
 * underlying fetch. IDLE timeout (reset per event), not a total-duration cap.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('../api', () => ({
  default: { defaults: { baseURL: '' }, get: vi.fn() },
}));

import { systemService, RESTORE_STALL_TIMEOUT_MS } from '../system';

/** A fetch Response whose body reader yields `firstChunk` once, then stalls. */
function makeStallingResponse(firstChunk: string, abortSpy?: () => void) {
  let readCount = 0;
  const reader = {
    read: vi.fn().mockImplementation(() => {
      readCount++;
      if (readCount === 1) {
        return Promise.resolve({ done: false, value: new TextEncoder().encode(firstChunk) });
      }
      // Second read never resolves — simulates a silently stalled backend stream.
      return new Promise(() => {});
    }),
    cancel: vi.fn(),
    releaseLock: vi.fn(),
  };
  return {
    ok: true,
    status: 200,
    body: { getReader: () => reader },
    _abortSpy: abortSpy,
  };
}

describe('restoreBackup() stall-guard', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('AC1: a stalled stream yields a terminal .error event after the idle timeout', async () => {
    const resp = makeStallingResponse('data: {"stage":"cloning","progress":10}\n');
    // @ts-expect-error test double
    global.fetch = vi.fn().mockResolvedValue(resp);

    const events: Array<{ stage: string; progress: number; error?: string }> = [];
    let finished = false;
    void (async () => {
      for await (const e of systemService.restoreBackup('https://x/repo.git')) {
        events.push(e as { stage: string; progress: number; error?: string });
        if (e.error) break;
      }
      finished = true;
    })();

    // Let fetch resolve + the first (progress) event flow through.
    await vi.advanceTimersByTimeAsync(1);
    expect(events.length).toBeGreaterThanOrEqual(1);
    expect(events.some((e) => e.error)).toBe(false); // not yet — still within idle window

    // Advance past the idle stall timeout — the guard must fire.
    await vi.advanceTimersByTimeAsync(RESTORE_STALL_TIMEOUT_MS + 1000);

    const errEvt = events.find((e) => e.error);
    expect(errEvt).toBeDefined();
    expect(errEvt!.error).toMatch(/stall|unreachable|no progress/i);
    // Terminal: consumer's `if (e.error) break` path completes the generator.
    expect(finished).toBe(true);
  });

  it('AC3: the idle timeout aborts the underlying fetch (releases the stream)', async () => {
    let aborted = false;
    const resp = makeStallingResponse('data: {"stage":"cloning","progress":5}\n');
    // @ts-expect-error test double
    global.fetch = vi.fn().mockImplementation((_url: string, init?: { signal?: AbortSignal }) => {
      init?.signal?.addEventListener('abort', () => { aborted = true; });
      return Promise.resolve(resp);
    });

    void (async () => {
      for await (const e of systemService.restoreBackup('https://x/repo.git')) {
        if (e.error) break;
      }
    })();

    await vi.advanceTimersByTimeAsync(1);
    expect(aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(RESTORE_STALL_TIMEOUT_MS + 1000);
    expect(aborted).toBe(true);
  });

  it('AC3b: an external abort signal (component unmount) exits a generator parked at reader.read()', async () => {
    // The REAL unmount path: .return() on a generator parked at `await reader.read()`
    // cannot abort it (queues behind the pending read). Only an external signal that
    // aborts the fetch can — the reader.read() then rejects and the generator exits.
    let fetchAborted = false;
    let rejectParkedRead: ((e: unknown) => void) | null = null;
    let readCount = 0;
    const reader = {
      read: vi.fn().mockImplementation(() => {
        readCount++;
        if (readCount === 1) {
          return Promise.resolve({ done: false, value: new TextEncoder().encode('data: {"stage":"cloning","progress":30}\n') });
        }
        // 2nd read parks — but is rejectable, exactly as a real fetch abort rejects it.
        return new Promise((_resolve, reject) => { rejectParkedRead = reject; });
      }),
      cancel: vi.fn(),
      releaseLock: vi.fn(),
    };
    // @ts-expect-error test double
    global.fetch = vi.fn().mockImplementation((_url: string, init?: { signal?: AbortSignal }) => {
      init?.signal?.addEventListener('abort', () => {
        fetchAborted = true;
        // Real fetch: aborting the signal rejects the in-flight reader.read().
        rejectParkedRead?.(new DOMException('aborted', 'AbortError'));
      });
      return Promise.resolve({ ok: true, status: 200, body: { getReader: () => reader } });
    });

    const externalController = new AbortController();
    const gen = systemService.restoreBackup('https://x/repo.git', undefined, externalController.signal);
    const first = await gen.next();
    expect(first.value?.progress).toBe(30);
    expect(fetchAborted).toBe(false);
    // Resume the generator so it calls (and PARKS on) the 2nd read — do NOT await
    // yet (it would hang). THEN fire the external signal (component unmount).
    const pending = gen.next();
    await Promise.resolve(); // let the generator reach the parked reader.read()
    externalController.abort();
    expect(fetchAborted).toBe(true); // fetch released
    // The parked read now rejects → generator must terminate (not hang).
    const next = await pending;
    expect(next.done).toBe(true);
  });

  it('AC1b: the idle timer RESETS on each event (a slow-but-progressing stream is NOT killed)', async () => {
    // Reader yields an event every (timeout - 10s), so it never idles long enough.
    let readCount = 0;
    const step = RESTORE_STALL_TIMEOUT_MS - 10_000;
    const reader = {
      read: vi.fn().mockImplementation(() => {
        readCount++;
        if (readCount <= 3) {
          return new Promise((resolve) =>
            setTimeout(
              () => resolve({ done: false, value: new TextEncoder().encode(`data: {"stage":"s","progress":${readCount * 20}}\n`) }),
              step,
            ),
          );
        }
        if (readCount === 4) return Promise.resolve({ done: true, value: undefined });
        return new Promise(() => {});
      }),
      cancel: vi.fn(),
      releaseLock: vi.fn(),
    };
    // @ts-expect-error test double
    global.fetch = vi.fn().mockResolvedValue({ ok: true, status: 200, body: { getReader: () => reader } });

    const events: Array<{ error?: string }> = [];
    let finished = false;
    void (async () => {
      for await (const e of systemService.restoreBackup('https://x/repo.git')) {
        events.push(e as { error?: string });
        if (e.error) break;
      }
      finished = true;
    })();

    // Advance through 3 slow-but-progressing reads + the done.
    await vi.advanceTimersByTimeAsync(step * 4 + 100);
    expect(events.some((e) => e.error)).toBe(false); // never stalled long enough
    expect(finished).toBe(true);
  });
});
