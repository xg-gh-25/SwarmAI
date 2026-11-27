/**
 * hiveService.listSsh: GET /api/hive/ssh gives this computer's own SSH certificate end once,
 * next to the machines; each machine it returns carries it (host_cert_valid_until), so a row
 * can say that this computer's login certificate ended.
 */
import { describe, expect, it, vi } from 'vitest';

const apiGet = vi.fn();
vi.mock('./api', () => ({ default: { get: (...a: unknown[]) => apiGet(...a) } }));

import { hiveService } from './hive';

describe('listSsh', () => {
  it('copies this computer\'s certificate end onto every machine', async () => {
    apiGet.mockResolvedValue({ data: { machines: [{ id: 'm1' }, { id: 'm2' }], host_cert_valid_until: 1764207573 } });
    const got = await hiveService.listSsh();
    expect(apiGet).toHaveBeenCalledWith('/hive/ssh');
    expect(got.map((m) => m.host_cert_valid_until)).toEqual([1764207573, 1764207573]);
  });

  it('null when this computer has no certificate, or the answer has none', async () => {
    apiGet.mockResolvedValue({ data: { machines: [{ id: 'm1' }], host_cert_valid_until: null } });
    expect((await hiveService.listSsh())[0].host_cert_valid_until).toBeNull();
    apiGet.mockResolvedValue({ data: { machines: [{ id: 'm1' }] } });
    expect((await hiveService.listSsh())[0].host_cert_valid_until).toBeNull();
  });
});
