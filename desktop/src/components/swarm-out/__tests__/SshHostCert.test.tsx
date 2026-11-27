/**
 * Swarm Out › settings › an SSH machine whose login certificate on THIS computer ended
 * (SshMachinesSection, hostCertEnded; GET /api/hive/ssh host_cert_valid_until).
 *
 * Questions: (1) while this computer's certificate has ended and the machine does not answer,
 * does the row say so (its state and a note with the time, without opening it) and does the
 * renew button type the machine's login_renew_hint into the terminal here, never over ssh;
 * (2) without a renew command, can it be set from the row, and where there is no terminal is
 * the command copied; (3) within an hour of the end, while the machine answers, is there a
 * warning, and nothing when the end is far or the machine answers past an old certificate?
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import i18n from '../../../i18n';

const mocks = { list: vi.fn(), options: vi.fn() };
vi.mock('../../../services/hive', async (orig) => ({
  ...(await orig<typeof import('../../../services/hive')>()),
  hiveService: {
    listSsh: (...a: unknown[]) => mocks.list(...a), setSshOptions: (...a: unknown[]) => mocks.options(...a),
    addSsh: vi.fn(), retrySsh: vi.fn(), removeSsh: vi.fn(),
  },
}));
vi.mock('../../settings/HiveBrains', () => ({ HiveBrains: () => null }));
const desktop = { on: true };
vi.mock('../../../services/tauri', () => ({ isDesktop: () => desktop.on }));
const copied = vi.fn(async (_: string) => true);
vi.mock('../../../utils/clipboard', () => ({ copyToClipboard: (s: string) => copied(s) }));

import { SshMachinesSection, forgetSettingsForTests } from '../SwarmOutSettings';
import { TERMINAL_RUN_EVENT } from '../../common/settingsEvents';

const now = Date.now() / 1000;
const machine = (over: Record<string, unknown> = {}, config: Record<string, unknown> = {}) => ({
  id: 'm1', name: 'dev-dsk', status: 'running', version: '2.6.0', instance_type: 'ssh', error_message: null,
  reachable: false, cert_valid_until: null, tunnel_error: 'Permission denied (publickey).',
  host_cert_valid_until: now - 6 * 3600,
  config: { ssh_host: 'dev-dsk.example.com', reaches_private: true, general_tasks: true, mounts: [], mounts_auto: true,
    cert_renew_hint: 'mwinit -o', login_renew_hint: 'mwinit', identity: 'rolesanywhere', not_after: now + 86400,
    renew_error: null, ...config },
  ...over,
});

beforeEach(async () => {
  forgetSettingsForTests();
  mocks.list.mockReset();
  mocks.options.mockReset().mockResolvedValue(undefined);
  copied.mockClear();
  desktop.on = true;
  await i18n.changeLanguage('en');
});

describe('this computer\'s SSH certificate', () => {
  it('an expired computer certificate is named, with its renew', async () => {
    mocks.list.mockResolvedValue([machine()]);
    const typed: { command: string; title: string }[] = [];
    const on = (e: Event) => typed.push((e as CustomEvent).detail);
    window.addEventListener(TERMINAL_RUN_EVENT, on);
    render(<SshMachinesSection onError={vi.fn()} />);
    const row = await screen.findByTestId('so-ssh-row');
    expect(within(row).getByTestId('so-ssh-state')).toHaveTextContent("this computer's certificate expired");
    expect(row.dataset.tone).toBe('error');
    expect(within(row).getByTestId('so-ssh-host-cert-note')).toHaveTextContent('cannot log in');
    fireEvent.click(within(row).getByTestId('so-ssh-host-renew'));
    window.removeEventListener(TERMINAL_RUN_EVENT, on);
    // the computer's own command, typed here: never `ssh <host> ...`
    expect(typed).toEqual([{ command: 'mwinit', title: 'This computer' }]);
    expect(within(row).getByTestId('so-ssh-host-renew-started')).toBeInTheDocument();
    // the rows are read again until the machine answers
    await waitFor(() => expect(mocks.list.mock.calls.length).toBeGreaterThan(1), { timeout: 4500 });
  });

  it('another outage, or a machine not probed yet, is not blamed on the certificate', async () => {
    mocks.list.mockResolvedValue([
      machine({ id: 'a', name: 'down-box', tunnel_error: 'connect to host dev-dsk port 22: Connection refused' }),
      machine({ id: 'b', name: 'new-box', reachable: null, tunnel_error: '' }),
    ]);
    render(<SshMachinesSection onError={vi.fn()} />);
    await waitFor(() => expect(screen.getAllByTestId('so-ssh-row')).toHaveLength(2));
    for (const row of screen.getAllByTestId('so-ssh-row')) {
      expect(within(row).queryByTestId('so-ssh-host-cert')).toBeNull();
      expect(within(row).getByTestId('so-ssh-state')).not.toHaveTextContent("this computer's certificate");
    }
  });

  it('the two renewals keep apart: one started does not show the other as started', async () => {
    // the machine's own certificate AND this computer's ended: start only the login renewal
    mocks.list.mockResolvedValue([machine({ cert_valid_until: now - 60 })]);
    render(<SshMachinesSection onError={vi.fn()} />);
    const row = await screen.findByTestId('so-ssh-row');
    fireEvent.click(within(row).getByTestId('so-ssh-host-renew'));
    expect(within(row).getByTestId('so-ssh-host-renew-started')).toBeInTheDocument();
    expect(within(row).queryByTestId('so-ssh-renew-started')).toBeNull();
  });

  it('a renewal started long ago is not shown as started when the certificate ends again', async () => {
    mocks.list.mockResolvedValue([machine({ cert_valid_until: now - 60 })]);
    render(<SshMachinesSection onError={vi.fn()} />);
    const row = await screen.findByTestId('so-ssh-row');
    fireEvent.click(within(row).getByTestId('so-ssh-host-renew'));
    fireEvent.click(within(row).getByTestId('so-ssh-renew-go'));
    expect(within(row).getByTestId('so-ssh-host-renew-started')).toBeInTheDocument();
    expect(within(row).getByTestId('so-ssh-renew-started')).toBeInTheDocument();
    // the rows are read again more than the watch window later: both certificates ended again
    const real = Date.now.bind(Date);
    const later = vi.spyOn(Date, 'now').mockImplementation(() => real() + 11 * 60_000);
    try {
      await waitFor(() => expect(within(row).queryByTestId('so-ssh-host-renew-started')).toBeNull(), { timeout: 4500 });
      expect(within(row).queryByTestId('so-ssh-renew-started')).toBeNull();
    } finally { later.mockRestore(); }
  });

  it('without a renew command, a form to set it; without a terminal, copied', async () => {
    mocks.list.mockResolvedValue([machine({}, { login_renew_hint: '' })]);
    render(<SshMachinesSection onError={vi.fn()} />);
    const row = await screen.findByTestId('so-ssh-row');
    expect(within(row).queryByTestId('so-ssh-host-renew')).toBeNull();
    fireEvent.click(within(row).getByTestId('so-ssh-login-hint-set'));
    fireEvent.change(within(row).getByTestId('so-ssh-login-hint'), { target: { value: ' mwinit ' } });
    fireEvent.click(within(row).getByTestId('so-ssh-login-hint-save'));
    await waitFor(() => expect(mocks.options).toHaveBeenCalledWith('m1', { login_renew_hint: 'mwinit' }));
  });

  it('where there is no terminal, the command is copied', async () => {
    desktop.on = false;
    mocks.list.mockResolvedValue([machine()]);
    render(<SshMachinesSection onError={vi.fn()} />);
    fireEvent.click(within(await screen.findByTestId('so-ssh-row')).getByTestId('so-ssh-host-renew'));
    await waitFor(() => expect(copied).toHaveBeenCalledWith('mwinit'));
  });

  it('within an hour of the end, a warning', async () => {
    mocks.list.mockResolvedValue([
      machine({ id: 'a', name: 'soon-box', reachable: true, tunnel_error: '', host_cert_valid_until: now + 20 * 60 }),
      machine({ id: 'b', name: 'far-box', reachable: true, tunnel_error: '', host_cert_valid_until: now + 6 * 3600 }),
      // answers although this computer's old certificate ended: it logs in some other way
      machine({ id: 'c', name: 'key-box', reachable: true, tunnel_error: '', host_cert_valid_until: now - 3600 }),
    ]);
    render(<SshMachinesSection onError={vi.fn()} />);
    await waitFor(() => expect(screen.getAllByTestId('so-ssh-row')).toHaveLength(3));
    const rows = screen.getAllByTestId('so-ssh-row');
    const by = (n: string) => rows.find((r) => r.textContent?.includes(n))!;
    expect(within(by('soon-box')).getByTestId('so-ssh-host-cert-note')).toHaveTextContent('ends at');
    expect(within(by('far-box')).queryByTestId('so-ssh-host-cert')).toBeNull();
    expect(within(by('key-box')).queryByTestId('so-ssh-host-cert')).toBeNull();
    expect(by('key-box').dataset.tone).toBe('ok');
  });
});
