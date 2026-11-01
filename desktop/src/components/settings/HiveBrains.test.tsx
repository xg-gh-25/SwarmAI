/**
 * Tests for HiveBrains — the Brains line of a running Hive card (project-agent-flow §6).
 * hiveService is mocked at the boundary.
 */
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { HiveBrainRow, HiveBrainStatus } from '../../services/hive';

const brainStatus = vi.fn();
const syncBrain = vi.fn();
const syncDefaultBrains = vi.fn();
vi.mock('../../services/hive', async () => {
  const actual = await vi.importActual<typeof import('../../services/hive')>('../../services/hive');
  return {
    ...actual,
    hiveService: {
      brainStatus: (id: string) => brainStatus(id),
      syncBrain: (id: string, b: string) => syncBrain(id, b),
      syncDefaultBrains: (id: string) => syncDefaultBrains(id),
    },
  };
});

import { HiveBrains, resetHiveBrainsAutoSync } from './HiveBrains';

const row = (o: Partial<HiveBrainRow>): HiveBrainRow => ({
  brain: 'Lib', agent: 'ready', localDigest: 'a'.repeat(64), localError: null,
  hiveDigest: 'a'.repeat(64), installedAt: '2026-10-29T01:00:00Z', synced: true,
  hiveChangedFiles: 0, running: 0, dispatchable: true, reasons: [], syncable: true,
  isDefault: false, ...o,
});
const status = (brains: HiveBrainRow[], supported = true): HiveBrainStatus => ({ hive: 'hive3', supported, brains });

const renderQ = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <HiveBrains instanceId="i1" />
    </QueryClientProvider>,
  );

beforeEach(() => {
  resetHiveBrainsAutoSync();
  syncBrain.mockResolvedValue(undefined);
  syncDefaultBrains.mockResolvedValue(undefined);
});
afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe('HiveBrains', () => {
  it('says how many Brains can be sent and how many need a sync, details behind a toggle', async () => {
    brainStatus.mockResolvedValue(status([
      row({ brain: 'SwarmAI', isDefault: true }),
      row({ brain: 'Lib', synced: false, hiveDigest: 'b'.repeat(64), dispatchable: false, reasons: ['out of date'] }),
      row({ brain: 'Draft', agent: 'invalid', synced: false, hiveDigest: null, dispatchable: false,
            syncable: false, reasons: ['it has no valid Project Agent', 'not synced'] }),
    ]));
    renderQ();
    const summary = await screen.findByTestId('hive-brains-summary');
    expect(summary.textContent).toContain('1 of 3 can be sent here');
    expect(summary.textContent).toContain('1 need a sync');
    expect(screen.queryByTestId('hive-brains-rows')).toBeNull();
    fireEvent.click(screen.getByTestId('hive-brains-toggle'));
    expect(screen.getByTestId('hive-brain-Lib').textContent).toContain('Out of date');
    expect(screen.getByTestId('hive-brain-Draft').textContent).toContain('it has no valid Project Agent');
    expect(screen.queryByTestId('hive-brain-sync-Draft')).toBeNull();   // an invalid one does not leave the host
    expect(syncDefaultBrains).not.toHaveBeenCalled();                    // the defaults are synced
  });

  it('syncs the out-of-date Brains and reloads', async () => {
    brainStatus.mockResolvedValueOnce(status([
      row({ brain: 'Lib', synced: false, hiveDigest: null, dispatchable: false, reasons: ['not synced'] }),
    ])).mockResolvedValue(status([row({ brain: 'Lib' })]));
    renderQ();
    fireEvent.click(await screen.findByTestId('hive-brains-sync-all'));
    await waitFor(() => expect(syncBrain).toHaveBeenCalledWith('i1', 'Lib'));
    await waitFor(() => expect(screen.getByTestId('hive-brains-summary').textContent).toContain('1 of 1'));
    expect(brainStatus).toHaveBeenCalledTimes(2);
  });

  it('shows why a sync failed', async () => {
    brainStatus.mockResolvedValue(status([
      row({ brain: 'Lib', synced: false, hiveDigest: null, dispatchable: false, reasons: ['not synced'] }),
    ]));
    syncBrain.mockRejectedValue(new Error('the Hive is busy with Lib'));
    renderQ();
    fireEvent.click(await screen.findByTestId('hive-brains-toggle'));
    fireEvent.click(screen.getByTestId('hive-brain-sync-Lib'));
    expect((await screen.findByTestId('hive-brains-sync-error')).textContent).toContain('busy');
  });

  it('syncs SwarmAI and General once per Hive when they are missing there', async () => {
    brainStatus.mockResolvedValue(status([
      row({ brain: 'SwarmAI', isDefault: true, synced: false, hiveDigest: null, dispatchable: false,
            reasons: ['not synced'] }),
    ]));
    renderQ();
    await waitFor(() => expect(syncDefaultBrains).toHaveBeenCalledWith('i1'));
    cleanup();
    renderQ();
    await screen.findByTestId('hive-brains-summary');
    expect(syncDefaultBrains).toHaveBeenCalledTimes(1);
  });

  it('an old Hive is told to update once; a failed read can be retried', async () => {
    brainStatus.mockResolvedValueOnce(status([], false));
    renderQ();
    expect((await screen.findByTestId('hive-brains-unsupported')).textContent).toContain('update it once');
    expect(syncDefaultBrains).not.toHaveBeenCalled();
    cleanup();
    brainStatus.mockRejectedValueOnce(new Error('the Hive did not answer')).mockResolvedValue(status([row({})]));
    renderQ();
    const err = await screen.findByTestId('hive-brains-error');
    fireEvent.click(err.querySelector('button')!);
    expect((await screen.findByTestId('hive-brains-summary')).textContent).toContain('1 of 1');
  });
});
