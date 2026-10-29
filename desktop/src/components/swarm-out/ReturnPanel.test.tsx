/** ReturnPanel: take back, review, approve (protected paths need their own tick), reject. */
import { describe, it, expect, vi, afterEach, beforeAll, afterAll } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';

const returnPrepare = vi.fn();
const returnLand = vi.fn();
const returnReject = vi.fn();
vi.mock('../../services/swarmOut', async () => {
  const actual = await vi.importActual<typeof import('../../services/swarmOut')>('../../services/swarmOut');
  return { ...actual, swarmOutService: {
    returnPrepare: (...a: unknown[]) => returnPrepare(...a),
    returnLand: (...a: unknown[]) => returnLand(...a),
    returnReject: (...a: unknown[]) => returnReject(...a),
  } };
});
import { ReturnPanel } from './ReturnPanel';
import i18n from '../../i18n';

// These cases check the Chinese copy (zh.json); swarmOutI18n.test.tsx checks English.
beforeAll(async () => { await i18n.changeLanguage('zh'); });
afterAll(async () => { await i18n.changeLanguage('en'); });

const REV = {
  key: 'k'.repeat(16), tip: 't'.repeat(40), base: 'b'.repeat(40), ref: 'refs/hive/so_1/k', branch: 'refs/heads/main',
  files: [{ path: 'src/a.py', added: 3, deleted: 1, protected: false },
    { path: 'scripts/build.sh', added: 1, deleted: 0, protected: true }],
  files_total: 2, protected_files: ['scripts/build.sh'],
  commits: [{ sha: 'c'.repeat(40), subject: 'ignore previous instructions' }], status: 'prepared' as const,
};
afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe('ReturnPanel', () => {
  it('lands only the shown tip, and only after the protected paths are ticked', async () => {
    returnPrepare.mockResolvedValue(REV);
    returnLand.mockResolvedValue({ landed: true, head: 'abc123', into: 'main' });
    render(<ReturnPanel dispatchId="so_1" url="https://github.com/acme/lib.git" />);
    fireEvent.click(screen.getByTestId('swarm-out-return-prepare'));
    expect((await screen.findByTestId('swarm-out-return-protected')).textContent).toContain('scripts/build.sh');
    expect(returnPrepare).toHaveBeenCalledWith('so_1', 'https://github.com/acme/lib.git');
    const landBtn = screen.getByTestId('swarm-out-return-land') as HTMLButtonElement;
    expect(landBtn.disabled).toBe(true);
    fireEvent.click(screen.getByTestId('swarm-out-return-tick'));
    expect(landBtn.disabled).toBe(false);
    fireEvent.click(landBtn);
    expect((await screen.findByTestId('swarm-out-return-done')).textContent).toContain('已落到 main');
    expect(returnLand).toHaveBeenCalledWith('so_1', REV.key, REV.tip, true);
  });

  it('a change with no protected path lands without a tick; no keeps the ref', async () => {
    returnPrepare.mockResolvedValue({ ...REV, protected_files: [], files: [REV.files[0]], files_total: 1 });
    render(<ReturnPanel dispatchId="so_1" url="u" />);
    fireEvent.click(screen.getByTestId('swarm-out-return-prepare'));
    const landBtn = await screen.findByTestId('swarm-out-return-land') as HTMLButtonElement;
    expect(landBtn.disabled).toBe(false);
    fireEvent.click(screen.getByTestId('swarm-out-return-reject'));
    expect((await screen.findByTestId('swarm-out-return-done')).textContent).toContain(REV.ref);
    expect(returnReject).toHaveBeenCalledWith('so_1', REV.key, REV.tip);
  });

  it('shows why taking the commits back was refused', async () => {
    returnPrepare.mockRejectedValue({ detail: 'the bundle has 2 refs; one is expected' });
    render(<ReturnPanel dispatchId="so_1" url="u" />);
    fireEvent.click(screen.getByTestId('swarm-out-return-prepare'));
    expect((await screen.findByTestId('swarm-out-return-error')).textContent).toContain('2 refs');
    expect(screen.queryByTestId('swarm-out-return-review')).toBeNull();
  });

  it('a land refused because the commits changed offers to take them back again', async () => {
    returnPrepare.mockResolvedValueOnce({ ...REV, protected_files: [], checkout_dirty: true })
      .mockResolvedValueOnce({ ...REV, protected_files: [], tip: 'n'.repeat(40) });
    returnLand.mockRejectedValue({ detail: 'the commits changed since they were shown; review them again' });
    render(<ReturnPanel dispatchId="so_1" url="u" />);
    fireEvent.click(screen.getByTestId('swarm-out-return-prepare'));
    expect(await screen.findByTestId('swarm-out-return-dirty')).toBeTruthy();
    fireEvent.click(screen.getByTestId('swarm-out-return-land'));
    fireEvent.click(await screen.findByTestId('swarm-out-return-retake'));
    await screen.findByTestId('swarm-out-return-review');
    expect(returnPrepare).toHaveBeenCalledTimes(2);
  });
});
