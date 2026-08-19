/**
 * Unit tests for backupToastFor — the pure toast-decision function for backup results.
 *
 * backupToastFor returns { severity, messageKey, messageParams? } (i18n keys, NOT finished
 * strings) so the component layer translates via t(messageKey, messageParams). This keeps
 * the function pure/unit-testable without mocking `t`.
 *
 * Also asserts key-completeness: every messageKey the function can return exists in BOTH
 * en.json and zh.json (a typo'd key would silently render the raw key string to the user).
 *
 * Mutation-provable: deleting the 'refused' branch or changing a messageKey makes a case RED.
 */
import { describe, it, expect } from 'vitest';
import { backupToastFor } from '../system';
import en from '../../i18n/locales/en.json';
import zh from '../../i18n/locales/zh.json';

// Resolve a dotted key path against a locale object; return undefined if any segment missing.
function resolveKey(locale: Record<string, unknown>, dotted: string): unknown {
  return dotted.split('.').reduce<unknown>((acc, seg) => {
    if (acc && typeof acc === 'object' && seg in (acc as Record<string, unknown>)) {
      return (acc as Record<string, unknown>)[seg];
    }
    return undefined;
  }, locale);
}

describe('backupToastFor', () => {
  it('ok → success, messageKey + params (tablesExported, commit)', () => {
    const t = backupToastFor({ status: 'ok', tablesExported: 3, commit: 'abc123', pushStatus: 'ok' });
    expect(t.severity).toBe('success');
    expect(t.messageKey).toBe('settings.backup.toast.ok');
    expect(t.messageParams).toEqual({ tablesExported: 3, commit: 'abc123' });
  });

  it('ok with null commit → commit param falls back to em-dash (no literal "null")', () => {
    const t = backupToastFor({ status: 'ok', tablesExported: 1, commit: null, pushStatus: 'ok' });
    expect(t.messageParams).toEqual({ tablesExported: 1, commit: '—' });
  });

  it('failed → warning', () => {
    const t = backupToastFor({ status: 'ok', tablesExported: 3, commit: 'abc', pushStatus: 'failed' });
    expect(t.severity).toBe('warning');
    expect(t.messageKey).toBe('settings.backup.toast.failed');
  });

  it('no_changes → info', () => {
    const t = backupToastFor({ status: 'ok', tablesExported: 0, commit: null, pushStatus: 'no_changes' });
    expect(t.severity).toBe('info');
    expect(t.messageKey).toBe('settings.backup.toast.noChanges');
  });

  it('skipped_disabled → info disabled', () => {
    const t = backupToastFor({ status: 'ok', tablesExported: 0, commit: null, pushStatus: 'skipped_disabled' });
    expect(t.severity).toBe('info');
    expect(t.messageKey).toBe('settings.backup.toast.disabled');
  });

  it('skipped → info disabled (same class)', () => {
    const t = backupToastFor({ status: 'ok', tablesExported: 0, commit: null, pushStatus: 'skipped' });
    expect(t.severity).toBe('info');
    expect(t.messageKey).toBe('settings.backup.toast.disabled');
  });

  it('refused + no_configured_destination → warning, reason-specific key', () => {
    const t = backupToastFor({ status: 'ok', tablesExported: 0, commit: null, pushStatus: 'refused', refuseReason: 'no_configured_destination' });
    expect(t.severity).toBe('warning');
    expect(t.messageKey).toBe('settings.backup.toast.refused.noConfiguredDestination');
  });

  it('refused + destination_mismatch → warning, distinct key', () => {
    const t = backupToastFor({ status: 'ok', tablesExported: 0, commit: null, pushStatus: 'refused', refuseReason: 'destination_mismatch' });
    expect(t.severity).toBe('warning');
    expect(t.messageKey).toBe('settings.backup.toast.refused.destinationMismatch');
  });

  it('refused + no_remote → warning, distinct key', () => {
    const t = backupToastFor({ status: 'ok', tablesExported: 0, commit: null, pushStatus: 'refused', refuseReason: 'no_remote' });
    expect(t.severity).toBe('warning');
    expect(t.messageKey).toBe('settings.backup.toast.refused.noRemote');
  });

  it('refused without refuseReason → warning, generic fallback key', () => {
    const t = backupToastFor({ status: 'ok', tablesExported: 0, commit: null, pushStatus: 'refused' });
    expect(t.severity).toBe('warning');
    expect(t.messageKey).toBe('settings.backup.toast.refused.fallback');
  });

  it('refused with UNKNOWN refuseReason → warning, generic fallback key', () => {
    const t = backupToastFor({ status: 'ok', tablesExported: 0, commit: null, pushStatus: 'refused', refuseReason: 'some_future_reason' });
    expect(t.severity).toBe('warning');
    expect(t.messageKey).toBe('settings.backup.toast.refused.fallback');
  });

  it('unknown push_status → info no-op key', () => {
    const t = backupToastFor({ status: 'ok', tablesExported: 0, commit: null, pushStatus: 'brand_new_status' });
    expect(t.severity).toBe('info');
    expect(t.messageKey).toBe('settings.backup.toast.noChanges');
  });
});

describe('backupToast key-completeness (en + zh aligned)', () => {
  // Every messageKey backupToastFor can return.
  const toastKeys = [
    'settings.backup.toast.ok',
    'settings.backup.toast.failed',
    'settings.backup.toast.noChanges',
    'settings.backup.toast.disabled',
    'settings.backup.toast.refused.noConfiguredDestination',
    'settings.backup.toast.refused.destinationMismatch',
    'settings.backup.toast.refused.noRemote',
    'settings.backup.toast.refused.fallback',
  ];
  // UI/interaction keys BackupTab consumes (static labels + catch-block errors).
  const uiKeys = [
    'settings.backup.title', 'settings.backup.active', 'settings.backup.disabled',
    'settings.backup.lastBackup', 'settings.backup.never', 'settings.backup.schedule',
    'settings.backup.dailyLabel', 'settings.backup.repository', 'settings.backup.notConfigured',
    'settings.backup.backupNow', 'settings.backup.backingUp', 'settings.backup.configure',
    'settings.backup.loading', 'settings.backup.configTitle', 'settings.backup.repoUrlLabel',
    'settings.backup.tokenLabel', 'settings.backup.tokenHint', 'settings.backup.saveConfig',
    'settings.backup.restoreTitle', 'settings.backup.restoreDesc', 'settings.backup.restore',
    'settings.backup.retryRestore', 'settings.backup.info',
    'settings.backup.toast.configSaved', 'settings.backup.toast.configFailed',
    'settings.backup.toast.enterRepoFirst', 'settings.backup.toast.backupFailed',
    'settings.backup.toast.restoreFailed',
  ];
  const allKeys = [...toastKeys, ...uiKeys];

  it('every key exists in en.json', () => {
    const missing = allKeys.filter((k) => resolveKey(en as Record<string, unknown>, k) === undefined);
    expect(missing).toEqual([]);
  });

  it('every key exists in zh.json', () => {
    const missing = allKeys.filter((k) => resolveKey(zh as Record<string, unknown>, k) === undefined);
    expect(missing).toEqual([]);
  });
});
