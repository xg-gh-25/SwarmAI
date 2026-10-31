/**
 * parseApiError: the reason the backend gave is the message, for every status
 * (design docs/design/2026-10-31-swarm-out-single-loop-fixes.md §2.4: a Swarm Out
 * refusal, "this question was already answered", reached the person as "Invalid
 * request"). The per-status text is only for a response that says nothing.
 */
import { describe, expect, it } from 'vitest';
import type { AxiosError } from 'axios';
import { parseApiError } from '../api';

function err(status: number, data: unknown): AxiosError {
  return { response: { status, data, headers: {} }, message: 'x' } as unknown as AxiosError;
}

describe('parseApiError', () => {
  it.each([400, 404, 409, 422, 500, 504])('uses a string detail as the message (%i)', (status) => {
    expect(parseApiError(err(status, { detail: 'this question was already answered' })).message)
      .toBe('this question was already answered');
  });

  it('uses detail.message when detail is an object', () => {
    expect(parseApiError(err(400, { detail: { code: 'X', message: 'pick one of the options' } })).message)
      .toBe('pick one of the options');
  });

  it('keeps the per-status text when the response says nothing', () => {
    expect(parseApiError(err(400, {})).message).toBe('Invalid request');
    expect(parseApiError(err(404, 'gone')).message).toBe('Resource not found');
    expect(parseApiError(err(400, { detail: '   ' })).message).toBe('Invalid request');
  });

  it('keeps the status code of the response', () => {
    expect(parseApiError(err(400, { detail: 'no' })).code).toBe('VALIDATION_FAILED');
  });
});
