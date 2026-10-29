import { describe, expect, it } from 'vitest';
import { ApiError } from '../../services/api';
import { failureReason } from './VirtualizedTree';

describe('failureReason', () => {
  it('shows the backend reason for a refused move, not the status text', () => {
    const err = new ApiError(
      { code: 'DUPLICATE_RESOURCE', message: 'Conflict', detail: 'Governed files cannot be moved out of their folder' },
      409,
    );
    expect(failureReason(err)).toBe('Governed files cannot be moved out of their folder');
  });

  it('falls back to the error message', () => {
    expect(failureReason(new Error('boom'))).toBe('boom');
    expect(failureReason('x')).toBe('Unknown error');
  });
});
