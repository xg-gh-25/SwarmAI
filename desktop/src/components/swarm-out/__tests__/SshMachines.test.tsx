/**
 * Swarm Out › settings › SSH machines (../SwarmOutSettings SshMachinesSection; design
 * 2026-11-24-swarm-out-ssh-machine §8).
 *
 * Questions: (1) does each machine show the one thing that decides whether it takes tasks
 * (running, certificate expired with a renew line that names no command, unreachable with
 * ssh's error, an install error with its retry); (2) does adding one ask for the host only and
 * send only it, after saying that tasks there run as the person (the AWS profile and the
 * directories are found on the machine); (3) does an opened machine show the profile and the
 * directories it uses, and do its private-network and general-tasks switches and delete reach
 * the backend; (4) can an expired certificate be renewed from here: the button types
 * `ssh -t <host> <renew command>` into the terminal (a shell on the machine when it has no renew
 * command; the command copied where there is no terminal), the rows are read again until the
 * new certificate shows, and a renew command can be set for a machine that has none?
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';

const mocks = { list: vi.fn(), add: vi.fn(), retry: vi.fn(), options: vi.fn(), remove: vi.fn() };
vi.mock('../../../services/hive', async (orig) => ({
  ...(await orig<typeof import('../../../services/hive')>()),
  hiveService: {
    listSsh: (...a: unknown[]) => mocks.list(...a), addSsh: (...a: unknown[]) => mocks.add(...a),
    retrySsh: (...a: unknown[]) => mocks.retry(...a), setSshOptions: (...a: unknown[]) => mocks.options(...a),
    removeSsh: (...a: unknown[]) => mocks.remove(...a),
  },
}));
vi.mock('../../settings/HiveBrains', () => ({ HiveBrains: () => null }));
const desktop = { on: true };
vi.mock('../../../services/tauri', () => ({ isDesktop: () => desktop.on }));
const copied = vi.fn(async (_: string) => true);
vi.mock('../../../utils/clipboard', () => ({ copyToClipboard: (s: string) => copied(s) }));

import { SshMachinesSection, forgetSettingsForTests, renewCommand } from '../SwarmOutSettings';
import { TERMINAL_RUN_EVENT } from '../../common/settingsEvents';

const now = Date.now() / 1000;
const base = {
  version: '2.5.5', instance_type: 'x86_64 · 4 vCPU · 15 GB', error_message: null, tunnel_error: '',
  config: { ssh_host: 'dev-dsk.example.com', reaches_private: true, general_tasks: true,
    mounts: ['/apollo:ro', '/workplace'], mounts_auto: true, cert_renew_hint: '',
    identity: 'rolesanywhere' as string | null, not_after: now + 20 * 86400 as number | null, renew_error: null as string | null },
};
const MACHINES = [
  { ...base, id: 'm1', name: 'ok-box', status: 'running', reachable: true, cert_valid_until: now + 3600 },
  { ...base, id: 'm2', name: 'expired-box', status: 'running', reachable: true, cert_valid_until: now - 60 },
  { ...base, id: 'm3', name: 'gone-box', status: 'running', reachable: false, cert_valid_until: null, tunnel_error: 'Permission denied (publickey).' },
  { ...base, id: 'm4', name: 'broken-box', status: 'error', reachable: null, cert_valid_until: null, error_message: 'image: docker did not build',
    config: { ...base.config, identity: null, not_after: null } },
  { ...base, id: 'm5', name: 'old-box', status: 'running', reachable: true, cert_valid_until: null,
    config: { ...base.config, identity: null, not_after: null, renew_error: null } },
];

beforeEach(() => {
  forgetSettingsForTests();                 // each case starts from an empty tab
  Object.values(mocks).forEach((m) => m.mockReset());
  mocks.list.mockResolvedValue(MACHINES);
  mocks.add.mockResolvedValue(undefined);
  mocks.options.mockResolvedValue(undefined);
  mocks.remove.mockResolvedValue({ left: [], data_kept: '' });
  mocks.retry.mockResolvedValue(undefined);
  desktop.on = true;
  copied.mockClear();
});

const row = (name: string) => screen.getAllByTestId('so-ssh-row').find((r) => r.textContent?.includes(name))!;

describe('SSH machines section', () => {
  it('renders ssh machines section', async () => {
    const onError = vi.fn();
    render(<SshMachinesSection onError={onError} />);
    await waitFor(() => expect(screen.getAllByTestId('so-ssh-row')).toHaveLength(5));
    expect(row('ok-box').dataset.tone).toBe('ok');
    // an expired certificate is the one thing to do, shown without opening the row
    expect(row('expired-box').dataset.tone).toBe('error');
    const renew = within(row('expired-box')).getByTestId('so-ssh-renew').textContent ?? '';
    expect(renew).toMatch(/SSH/);
    expect(renew).not.toMatch(/mwinit/);
    // unreachable: ssh's own error, when opened
    expect(row('gone-box').dataset.tone).toBe('error');
    fireEvent.click(within(row('gone-box')).getByRole('button', { name: 'gone-box' }));
    expect(within(row('gone-box')).getByTestId('so-ssh-tunnel').textContent).toContain('Permission denied');
    // an install error: its reason and a retry
    fireEvent.click(within(row('broken-box')).getByRole('button', { name: 'broken-box' }));
    expect(within(row('broken-box')).getByTestId('so-ssh-error').textContent).toContain('image:');
    fireEvent.click(within(row('broken-box')).getByTestId('so-ssh-retry'));
    await waitFor(() => expect(mocks.retry).toHaveBeenCalledWith('m4'));

    // what the machine uses, found on it; its switches and delete reach the backend
    fireEvent.click(within(row('ok-box')).getByRole('button', { name: 'ok-box' }));
    // its own AWS identity: the certificate's end, renewed by itself; a machine installed on a
    // profile of the person's says it moves at its next update; one not installed, at install
    expect(within(row('ok-box')).getByTestId('so-ssh-aws-identity').textContent).toMatch(/renews by itself/);
    expect(within(row('broken-box')).getByTestId('so-ssh-aws-identity').textContent).toMatch(/set up at install/);
    fireEvent.click(within(row('old-box')).getByRole('button', { name: 'old-box' }));
    expect(within(row('old-box')).getByTestId('so-ssh-aws-identity').textContent).toMatch(/next update/);
    expect(within(row('ok-box')).getByTestId('so-ssh-mounts').textContent).toContain('/apollo:ro');
    expect(within(row('ok-box')).getByTestId('so-ssh-mounts').textContent).toContain('/workplace');
    fireEvent.click(within(row('ok-box')).getByTestId('so-ssh-private'));
    await waitFor(() => expect(mocks.options).toHaveBeenCalledWith('m1', { reaches_private: false }));
    fireEvent.click(within(row('ok-box')).getByTestId('so-ssh-general'));
    await waitFor(() => expect(mocks.options).toHaveBeenCalledWith('m1', { general_tasks: false }));
    fireEvent.click(within(row('ok-box')).getByTestId('so-ssh-delete'));
    fireEvent.click(within(row('ok-box')).getByTestId('so-ssh-delete-go'));
    await waitFor(() => expect(mocks.remove).toHaveBeenCalledWith('m1'));

    // adding one: the identity note is shown, the host is the one field and the one thing sent
    fireEvent.click(screen.getByTestId('so-ssh-add'));
    const form = screen.getByTestId('so-ssh-form');
    expect(screen.getByTestId('so-ssh-identity')).toBeTruthy();
    expect(within(form).getAllByRole('textbox')).toHaveLength(1);
    expect(within(form).queryAllByRole('checkbox')).toHaveLength(0);
    expect((screen.getByTestId('so-ssh-add-go') as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByTestId('so-ssh-host'), { target: { value: ' dev-dsk-2.example.com ' } });
    fireEvent.click(screen.getByTestId('so-ssh-add-go'));
    await waitFor(() => expect(mocks.add).toHaveBeenCalledWith({ ssh_host: 'dev-dsk-2.example.com' }));
    expect(onError).not.toHaveBeenCalledWith(expect.stringMatching(/./));
  });
});

describe('renewing an expired certificate from here', () => {
  it('builds the ssh command: the renew command quoted as one word, a shell without one', () => {
    expect(renewCommand('dev-dsk.example.com', 'mwinit -o')).toBe("ssh -t dev-dsk.example.com 'mwinit -o'");
    expect(renewCommand('me@box', '')).toBe('ssh me@box');
    expect(renewCommand('box', null)).toBe('ssh box');
    expect(renewCommand('box', "echo 'hi'")).toBe("ssh -t box 'echo '\\''hi'\\'''");
  });

  it('types the command into the terminal, then reads the rows again until the new certificate shows', async () => {
    const expired = { ...MACHINES[1], config: { ...base.config, cert_renew_hint: 'mwinit -o' } };
    mocks.list.mockResolvedValue([expired]);
    const runs: Array<{ command: string; title: string }> = [];
    const onRun = (e: Event) => runs.push((e as CustomEvent).detail);
    window.addEventListener(TERMINAL_RUN_EVENT, onRun);
    try {
      render(<SshMachinesSection onError={vi.fn()} />);
      const go = await screen.findByTestId('so-ssh-renew-go');
      expect(screen.getByTestId('so-ssh-renew').textContent).toContain('mwinit -o');
      expect(screen.queryByTestId('so-ssh-renew-check')).toBeNull();
      fireEvent.click(go);
      expect(runs).toEqual([{ command: "ssh -t dev-dsk.example.com 'mwinit -o'", title: 'expired-box' }]);
      expect(screen.getByTestId('so-ssh-renew-started')).toBeTruthy();
      // watched: read again by itself, and once the machine has the new certificate it is ok
      const calls = mocks.list.mock.calls.length;
      mocks.list.mockResolvedValue([{ ...expired, cert_valid_until: Date.now() / 1000 + 43200 }]);
      await waitFor(() => expect(mocks.list.mock.calls.length).toBeGreaterThan(calls), { timeout: 4500 });
      await waitFor(() => expect(row('expired-box').dataset.tone).toBe('ok'));
      expect(screen.queryByTestId('so-ssh-renew-go')).toBeNull();
    } finally {
      window.removeEventListener(TERMINAL_RUN_EVENT, onRun);
    }
  });

  it('with no renew command: a shell on the machine, and the renew command can be set', async () => {
    const runs: string[] = [];
    const onRun = (e: Event) => runs.push((e as CustomEvent).detail.command);
    window.addEventListener(TERMINAL_RUN_EVENT, onRun);
    try {
      render(<SshMachinesSection onError={vi.fn()} />);
      const box = await waitFor(() => within(row('expired-box')).getByTestId('so-ssh-renew-box'));
      fireEvent.click(within(box).getByTestId('so-ssh-renew-go'));
      expect(runs).toEqual(['ssh dev-dsk.example.com']);
      fireEvent.click(within(box).getByTestId('so-ssh-renew-check'));
      fireEvent.click(within(box).getByTestId('so-ssh-hint-set'));
      expect((screen.getByTestId('so-ssh-hint-save') as HTMLButtonElement).disabled).toBe(true);
      fireEvent.change(screen.getByTestId('so-ssh-hint'), { target: { value: ' mwinit -o ' } });
      fireEvent.click(screen.getByTestId('so-ssh-hint-save'));
      await waitFor(() => expect(mocks.options).toHaveBeenCalledWith('m2', { cert_renew_hint: 'mwinit -o' }));
    } finally {
      window.removeEventListener(TERMINAL_RUN_EVENT, onRun);
    }
  });

  it('where there is no terminal, the command is copied', async () => {
    desktop.on = false;
    const runs: unknown[] = [];
    const onRun = (e: Event) => runs.push(e);
    window.addEventListener(TERMINAL_RUN_EVENT, onRun);
    try {
      render(<SshMachinesSection onError={vi.fn()} />);
      const box = await waitFor(() => within(row('expired-box')).getByTestId('so-ssh-renew-box'));
      fireEvent.click(within(box).getByTestId('so-ssh-renew-go'));
      await waitFor(() => expect(copied).toHaveBeenCalledWith('ssh dev-dsk.example.com'));
      expect(runs).toHaveLength(0);
    } finally {
      window.removeEventListener(TERMINAL_RUN_EVENT, onRun);
    }
  });
});
