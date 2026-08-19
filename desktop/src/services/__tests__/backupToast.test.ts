/**
 * Unit tests for backupToastFor — the pure toast-decision function for backup results.
 *
 * Covers all 6 backend push_status values + the 3 refuse_reason variants, so a
 * fail-closed backup refusal (push_status='refused') is surfaced to the user with an
 * actionable reason instead of being masked as "No changes to backup." (the pre-fix bug).
 *
 * Mutation-provable: deleting the 'refused' branch or flipping a severity makes a case RED.
 */
import { describe, it, expect } from 'vitest';
import { backupToastFor } from '../system';

describe('backupToastFor', () => {
  it('ok → success with tables + commit', () => {
    const t = backupToastFor({ status: 'ok', tablesExported: 3, commit: 'abc123', pushStatus: 'ok' });
    expect(t.severity).toBe('success');
    expect(t.message).toContain('3');
    expect(t.message).toContain('abc123');
  });

  it('failed → warning about push', () => {
    const t = backupToastFor({ status: 'ok', tablesExported: 3, commit: 'abc', pushStatus: 'failed' });
    expect(t.severity).toBe('warning');
    expect(t.message.toLowerCase()).toContain('push');
  });

  it('no_changes → info "No changes"', () => {
    const t = backupToastFor({ status: 'ok', tablesExported: 0, commit: null, pushStatus: 'no_changes' });
    expect(t.severity).toBe('info');
    expect(t.message).toContain('No changes');
  });

  it('skipped_disabled → info, guides to enable in Settings', () => {
    const t = backupToastFor({ status: 'ok', tablesExported: 0, commit: null, pushStatus: 'skipped_disabled' });
    expect(t.severity).toBe('info');
    expect(t.message.toLowerCase()).toContain('disabled');
    expect(t.message).toContain('Settings');
  });

  it('skipped → info disabled (same class as skipped_disabled)', () => {
    const t = backupToastFor({ status: 'ok', tablesExported: 0, commit: null, pushStatus: 'skipped' });
    expect(t.severity).toBe('info');
    expect(t.message.toLowerCase()).toContain('disabled');
  });

  it('refused + no_configured_destination → warning, guides to configure', () => {
    const t = backupToastFor({ status: 'ok', tablesExported: 0, commit: null, pushStatus: 'refused', refuseReason: 'no_configured_destination' });
    expect(t.severity).toBe('warning');
    expect(t.message.toLowerCase()).toContain('not configured');
    expect(t.message).toContain('Settings');
  });

  it('refused + destination_mismatch → warning, mentions mismatch (NOT a generic configure prompt)', () => {
    const t = backupToastFor({ status: 'ok', tablesExported: 0, commit: null, pushStatus: 'refused', refuseReason: 'destination_mismatch' });
    expect(t.severity).toBe('warning');
    expect(t.message.toLowerCase()).toContain('mismatch');
  });

  it('refused + no_remote → warning, mentions remote', () => {
    const t = backupToastFor({ status: 'ok', tablesExported: 0, commit: null, pushStatus: 'refused', refuseReason: 'no_remote' });
    expect(t.severity).toBe('warning');
    expect(t.message.toLowerCase()).toContain('remote');
  });

  it('refused without refuseReason → warning, generic fallback (no undefined leak)', () => {
    const t = backupToastFor({ status: 'ok', tablesExported: 0, commit: null, pushStatus: 'refused' });
    expect(t.severity).toBe('warning');
    expect(t.message.toLowerCase()).toContain('refused');
    expect(t.message).not.toContain('undefined');
  });

  it('refused with UNKNOWN refuseReason → warning, generic fallback (not undefined)', () => {
    const t = backupToastFor({ status: 'ok', tablesExported: 0, commit: null, pushStatus: 'refused', refuseReason: 'some_future_reason' });
    expect(t.severity).toBe('warning');
    expect(t.message.toLowerCase()).toContain('refused');
    expect(t.message).not.toContain('undefined');
    expect(t.message).not.toContain('some_future_reason');
  });

  it('unknown push_status → info no-op fallback', () => {
    const t = backupToastFor({ status: 'ok', tablesExported: 0, commit: null, pushStatus: 'brand_new_status' });
    expect(t.severity).toBe('info');
    expect(t.message).toContain('No changes');
  });
});
