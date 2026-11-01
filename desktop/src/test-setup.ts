/**
 * Vitest setup file
 * Extends expect with jest-dom matchers
 */
import '@testing-library/jest-dom/vitest';
// The app's i18n (English for jsdom's en-US): components render their real text, as in
// the app. A test that mocks react-i18next still gets its mock.
import './i18n';
import { afterEach } from 'vitest';
import { configure } from '@testing-library/react';

/**
 * DOM waits (waitFor / findBy*) get 5s instead of Testing Library's 1s default.
 * The full suite runs files in parallel workers and, under that load, a render that
 * takes ~50ms alone was seen to miss the 1s window (FileViewer.closeguard, twice:
 * 'Unable to find unsaved-warning-discard'). A passing wait returns as soon as its
 * condition holds, so this costs nothing unless a test is genuinely failing - it
 * then reports after 5s instead of 1s. Same reasoning as testTimeout: 15_000.
 */
configure({ asyncUtilTimeout: 5000 });
import { messageStoreRegistry } from './stores/MessageStore';
import { resetZoneMemory } from './pages/chat/components/TurnZones';
import { sessionEvents } from './services/sessionEvents';

/**
 * Global teardown — destroy all MessageStores after every test.
 *
 * MessageStore arms real setTimeout timers: a 100ms rAF fallback and a
 * DEFAULT_WATCHDOG_MS=90_000 (90s) streaming watchdog. Tests that drive
 * streaming through `messageStoreRegistry` (directly, or indirectly via
 * ChatPage / useChatStreamingLifecycle) arm the 90s watchdog; if the test
 * doesn't dispose the store, that pending timer keeps the worker process
 * alive — so `vitest run` over src/pages/chat + src/stores PASSES then hangs
 * up to 90s waiting to exit (the bulk-run "hang"). Clearing the registry here
 * calls destroy() on every store (clears both timers) so the process exits.
 * Idempotent — files that already clear() in their own afterEach are unaffected.
 */
afterEach(() => {
  messageStoreRegistry.clear();
  // The chat view's per-message memory (durations, merges) is module-level and keyed
  // by message id; test cases reuse ids, so each case starts from an empty memory.
  resetZoneMemory();
  // The window's session subscription is a module singleton the app never stops
  // (useChatStreamingLifecycle). A test that mounts it leaves a reconnect timer that
  // fires after the test environment is torn down ("window is not defined" in
  // getApiBaseUrl, an unhandled error that fails the run). Files that mock the
  // module may not provide stop().
  sessionEvents.stop?.();
});

/**
 * Suppress JSDOM + Node.js undici compatibility errors.
 *
 * JSDOM's internal undici dispatcher throws "invalid onError method"
 * InvalidArgumentError rejections that are unhandled. These don't affect
 * test correctness but cause Vitest to intermittently fail test files
 * when it catches enough unhandled rejections.
 *
 * See: https://github.com/jsdom/jsdom/issues/3750
 */
process.on('unhandledRejection', (reason: unknown) => {
  if (
    reason instanceof Error &&
    reason.message?.includes('invalid onError method')
  ) {
    // Silently swallow JSDOM undici dispatcher errors
    return;
  }
  // Re-throw everything else so real test failures aren't hidden
  throw reason;
});

/**
 * Global fetch mock to prevent JSDOM from dispatching real HTTP requests
 * via its undici-based resource loader, which is the root trigger for the
 * "invalid onError method" rejections.
 *
 * Only applied in jsdom environment — contract tests use @vitest-environment node
 * and need real fetch for HTTP fixture server communication.
 */
const isJSDOM = typeof globalThis.document !== 'undefined';
if (isJSDOM && (typeof globalThis.fetch === 'undefined' || globalThis.fetch)) {
  globalThis.fetch = Object.assign(
    async (_input: RequestInfo | URL, _init?: RequestInit): Promise<Response> => {
      return new Response(null, { status: 200 });
    },
    { __vitest_mock__: true },
  ) as typeof globalThis.fetch;
}
