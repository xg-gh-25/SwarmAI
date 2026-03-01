/**
 * Property-based tests for structured 404 error detection.
 *
 * What is being tested:
 * - ``isNotFoundError`` from ``useChatStreamingLifecycle`` — verifying that
 *   the function correctly identifies 404 errors via structured properties
 *   (Axios-style ``response.status`` and top-level ``status``) and returns
 *   ``false`` for errors without structured status information.
 *
 * Testing methodology: Property-based testing with fast-check + Vitest
 *
 * Key properties verified:
 * - Property 4: Structured 404 Detection — ``isNotFoundError`` returns
 *   ``true`` iff the error has a numeric status of 404; returns ``false``
 *   for errors without structured status (including plain Error instances
 *   with arbitrary messages containing "404").
 */

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import { isNotFoundError } from '../../hooks/useChatStreamingLifecycle';

// ---------------------------------------------------------------------------
// fast-check Arbitraries
// ---------------------------------------------------------------------------

/** Arbitrary for a non-404 HTTP status code (1xx–5xx range). */
const arbNon404Status = fc
  .integer({ min: 100, max: 599 })
  .filter((s) => s !== 404);

/** Arbitrary for any integer status code (wider range for edge cases). */
const arbAnyStatus = fc.integer({ min: -1000, max: 1000 });

/** Arbitrary for a random string that may or may not contain "404". */
const arbArbitraryMessage = fc.oneof(
  fc.string({ minLength: 0, maxLength: 200 }),
  fc.constant('404'),
  fc.constant('Not Found'),
  fc.constant('Error 404: resource not found'),
  fc.constant('Something went wrong'),
);

/**
 * Arbitrary for an Axios-style error: ``{ response: { status: number } }``.
 * The status is drawn from the full integer range to test edge cases.
 */
const arbAxiosError = (status: fc.Arbitrary<number>) =>
  fc.record({
    response: fc.record({ status }),
    message: arbArbitraryMessage,
  });

/**
 * Arbitrary for a custom API error with a top-level ``status`` property.
 */
const arbCustomApiError = (status: fc.Arbitrary<number>) =>
  fc.record({
    status,
    message: arbArbitraryMessage,
  });

/**
 * Arbitrary for a plain Error with an arbitrary message string.
 * These have no structured status and should always return false.
 */
const arbPlainError = arbArbitraryMessage.map((msg) => new Error(msg));

// ---------------------------------------------------------------------------
// Property 4: Structured 404 Detection
// ---------------------------------------------------------------------------

describe('Feature: chat-experience-cleanup, Property 4: Structured 404 Detection', () => {
  /**
   * **Validates: Requirements 4.2**
   *
   * Axios-style errors with response.status === 404 are detected as 404.
   */
  it('returns true for Axios-style errors with response.status === 404', () => {
    fc.assert(
      fc.property(arbArbitraryMessage, (message) => {
        const err = { response: { status: 404 }, message };
        expect(isNotFoundError(err)).toBe(true);
      }),
      { numRuns: 100 },
    );
  });

  /**
   * **Validates: Requirements 4.2**
   *
   * Axios-style errors with response.status !== 404 are NOT detected as 404.
   */
  it('returns false for Axios-style errors with response.status !== 404', () => {
    fc.assert(
      fc.property(arbAxiosError(arbNon404Status), (err) => {
        expect(isNotFoundError(err)).toBe(false);
      }),
      { numRuns: 100 },
    );
  });

  /**
   * **Validates: Requirements 4.2**
   *
   * Custom API errors with status === 404 are detected as 404.
   */
  it('returns true for custom API errors with status === 404', () => {
    fc.assert(
      fc.property(arbArbitraryMessage, (message) => {
        const err = { status: 404, message };
        expect(isNotFoundError(err)).toBe(true);
      }),
      { numRuns: 100 },
    );
  });

  /**
   * **Validates: Requirements 4.2**
   *
   * Custom API errors with status !== 404 are NOT detected as 404.
   */
  it('returns false for custom API errors with status !== 404', () => {
    fc.assert(
      fc.property(arbCustomApiError(arbNon404Status), (err) => {
        expect(isNotFoundError(err)).toBe(false);
      }),
      { numRuns: 100 },
    );
  });

  /**
   * **Validates: Requirements 4.3**
   *
   * Plain Error instances (no structured status) always return false,
   * even when the message string contains "404" or "not found".
   */
  it('returns false for plain Error instances regardless of message content', () => {
    fc.assert(
      fc.property(arbPlainError, (err) => {
        expect(isNotFoundError(err)).toBe(false);
      }),
      { numRuns: 100 },
    );
  });

  /**
   * **Validates: Requirements 4.2, 4.3**
   *
   * Universal property: for any error with a numeric status, isNotFoundError
   * returns true iff status === 404. For errors without structured status,
   * it always returns false.
   */
  it('returns true iff structured status === 404 across all error shapes', () => {
    // Arbitrary that produces one of: Axios-style, custom API, or plain Error
    const arbErrorWithExpected = fc.oneof(
      // Axios-style with random status
      arbAnyStatus.map((status) => ({
        err: { response: { status }, message: 'test' },
        expected: status === 404,
      })),
      // Custom API error with random status
      arbAnyStatus.map((status) => ({
        err: { status, message: 'test' } as unknown,
        expected: status === 404,
      })),
      // Plain Error — never 404
      arbArbitraryMessage.map((msg) => ({
        err: new Error(msg) as unknown,
        expected: false,
      })),
      // Bare object with no status fields — never 404
      fc.record({ foo: fc.string() }).map((obj) => ({
        err: obj as unknown,
        expected: false,
      })),
      // null and undefined — never 404
      fc.constant({ err: null as unknown, expected: false }),
      fc.constant({ err: undefined as unknown, expected: false }),
      // String — never 404
      fc.string().map((s) => ({
        err: s as unknown,
        expected: false,
      })),
    );

    fc.assert(
      fc.property(arbErrorWithExpected, ({ err, expected }) => {
        expect(isNotFoundError(err)).toBe(expected);
      }),
      { numRuns: 200 },
    );
  });

  /**
   * **Validates: Requirements 4.2**
   *
   * When an error has BOTH response.status and top-level status,
   * response.status takes precedence (Axios-style check runs first).
   */
  it('prioritizes response.status over top-level status', () => {
    // response.status is 404, top-level status is not
    const err1 = { response: { status: 404 }, status: 500 };
    expect(isNotFoundError(err1)).toBe(true);

    // response.status is 500, top-level status is 404
    const err2 = { response: { status: 500 }, status: 404 };
    expect(isNotFoundError(err2)).toBe(false);
  });
});
