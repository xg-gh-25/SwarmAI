/**
 * Vitest setup file
 * Extends expect with jest-dom matchers
 */
import '@testing-library/jest-dom/vitest';
import { afterEach } from 'vitest';
import { messageStoreRegistry } from './stores/MessageStore';

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
