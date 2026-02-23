/**
 * Vitest setup file
 * Extends expect with jest-dom matchers
 */
import '@testing-library/jest-dom/vitest';

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
 */
if (typeof globalThis.fetch === 'undefined' || globalThis.fetch) {
  globalThis.fetch = Object.assign(
    async (_input: RequestInfo | URL, _init?: RequestInit): Promise<Response> => {
      return new Response(null, { status: 200 });
    },
    { __vitest_mock__: true },
  ) as typeof globalThis.fetch;
}
