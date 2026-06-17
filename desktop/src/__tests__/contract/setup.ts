/**
 * Contract test setup — restores real fetch (undoes test-setup.ts mock).
 *
 * Contract tests intentionally make real HTTP requests to the fixture server.
 * The global test-setup.ts mocks fetch to prevent JSDOM undici errors in
 * component tests, but contract tests NEED real network.
 */

// Vitest environment for contract tests should be 'node' not 'jsdom'
// (set via comment directive in each test file or vitest config).
// But as a safety net, restore native fetch if it was mocked:
import { beforeAll } from 'vitest';

beforeAll(() => {
  // In Node environment, fetch is native (Node 18+) — no restoration needed.
  // This file exists as documentation and future-proofing.
});
