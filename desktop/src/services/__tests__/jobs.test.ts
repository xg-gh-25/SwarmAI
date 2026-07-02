/**
 * Tests for jobToCamelCase — the backend→frontend job-status mapping.
 *
 * Regression guard: this mapping previously dropped `enabled` and `last_run`,
 * so the Radar attention queue could not exclude disabled jobs (brain-push
 * appeared in "Needs You" despite being deliberately halted). These assert the
 * two new fields are surfaced AND that `enabled` fails OPEN.
 */
import { describe, it, expect } from 'vitest';
import { jobToCamelCase } from '../jobs';

describe('jobToCamelCase', () => {
  it('maps enabled + last_run from the backend snake_case payload', () => {
    const got = jobToCamelCase({
      id: 'os-eval',
      name: 'OS Eval',
      consecutive_failures: 2,
      enabled: true,
      last_run: '2026-07-02T04:28:03Z',
    });
    expect(got).toEqual({
      id: 'os-eval',
      name: 'OS Eval',
      consecutiveFailures: 2,
      enabled: true,
      lastRun: '2026-07-02T04:28:03Z',
    });
  });

  it('surfaces enabled=false for a disabled job (brain-push)', () => {
    const got = jobToCamelCase({ id: 'brain-push', enabled: false, consecutive_failures: 1 });
    expect(got.enabled).toBe(false);
  });

  it('fails OPEN: enabled defaults to true when the field is absent', () => {
    const got = jobToCamelCase({ id: 'mystery', consecutive_failures: 3 });
    expect(got.enabled).toBe(true);
    expect(got.lastRun).toBeNull();
  });

  it('only an explicit false disables — truthy/undefined stays enabled', () => {
    expect(jobToCamelCase({ id: 'a', enabled: undefined }).enabled).toBe(true);
    expect(jobToCamelCase({ id: 'b' }).enabled).toBe(true);
  });
});
