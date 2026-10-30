/**
 * UserMessageView × correction ledger (design 2026-10-30-autonomy-tenets-gaps §2).
 *
 * What is tested: the picker shows under the message the backend recorded (matched by
 * client id) and under no other; a click sends the person's choice with the user-action
 * mark and the line goes away; a failed save says so and keeps the line.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, act, waitFor } from '@testing-library/react';
import i18n from '../../../../i18n';
import type { Message } from '../../../../types';

const get = vi.fn();
const post = vi.fn();
vi.mock('../../../../services/api', () => ({ default: { get: (...a: unknown[]) => get(...a), post: (...a: unknown[]) => post(...a) } }));

import { UserMessageView } from '../UserMessageView';
import { _resetCorrectionsCache } from '../../../../services/corrections';

class ResizeObserverStub { observe() {} unobserve() {} disconnect() {} }
(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;

const msg = (cid: string, agoMs = 60_000): Message => ({
  id: cid, role: 'user', timestamp: new Date(Date.now() - agoMs).toISOString(),
  content: [{ type: 'text', text: '不对，按到账日' }], metadata: { client_id: cid },
});

const ROW = { id: 'cor-1', at: '2026-10-30T06:00:00Z', surface: 'chat', client_id: 'c-1', text: '不对，按到账日', candidates: [
  { key: 'Projects/B/2-understanding/IMPROVEMENT.md#What Failed#cand:x', kind: 'entry', title: 'Refunds counted by order date' }] };

beforeEach(() => {
  _resetCorrectionsCache();
  get.mockReset(); post.mockReset();
  get.mockResolvedValue({ data: { corrections: [ROW] } });
});
afterEach(async () => { await act(async () => { await i18n.changeLanguage('en'); }); });

describe('correction picker under a user bubble', () => {
  it('shows for the recorded message only, and a click records the choice', async () => {
    post.mockResolvedValue({ data: {} });
    render(<><UserMessageView message={msg('c-1')} sessionId="S1" /><UserMessageView message={msg('c-2')} sessionId="S1" /></>);
    const pick = await screen.findByRole('button', { name: 'Knowledge entry: Refunds counted by order date' });
    expect(screen.getAllByText('Correcting me? File it under:')).toHaveLength(1);
    expect(get).toHaveBeenCalledWith('/ingestion/corrections/pending', { params: { session_id: 'S1' } });
    fireEvent.click(pick);
    await waitFor(() => expect(screen.queryByText('Correcting me? File it under:')).toBeNull());
    const [url, body, cfg] = post.mock.calls[0];
    expect(url).toBe('/ingestion/corrections/cor-1/confirm');
    expect(body).toEqual({ choice: ROW.candidates[0].key });
    expect((cfg as { headers: Record<string, string> }).headers['X-Swarm-User-Action']).toBe('correction');
  });

  it('an old message gets no line: what still waits is in the CDF list', async () => {
    render(<UserMessageView message={msg('c-1', 11 * 60_000)} sessionId="S1" />);
    await act(async () => { await Promise.resolve(); });
    expect(get).not.toHaveBeenCalled();
    expect(screen.queryByText('Correcting me? File it under:')).toBeNull();
  });

  it('a row classified elsewhere (409) counts as done, with no error', async () => {
    post.mockRejectedValue(Object.assign(new Error('conflict'), { isAxiosError: true, response: { status: 409 } }));
    render(<UserMessageView message={msg('c-1')} sessionId="S1" />);
    fireEvent.click(await screen.findByRole('button', { name: 'not a correction' }));
    await waitFor(() => expect(screen.queryByText('Correcting me? File it under:')).toBeNull());
    expect(screen.queryByText('Not saved, click again')).toBeNull();
  });

  it('a failed save says so and keeps the line', async () => {
    post.mockRejectedValue(new Error('boom'));
    render(<UserMessageView message={msg('c-1')} sessionId="S1" />);
    fireEvent.click(await screen.findByRole('button', { name: 'a new kind' }));
    expect(await screen.findByText('Not saved, click again')).toBeTruthy();
    expect(post.mock.calls[0][1]).toEqual({ choice: 'new' });
    expect(screen.getByText('Correcting me? File it under:')).toBeTruthy();
  });

  it('nothing without a session', async () => {
    render(<UserMessageView message={msg('c-1')} />);
    await act(async () => { await Promise.resolve(); });
    expect(get).not.toHaveBeenCalled();
    expect(screen.queryByText('Correcting me? File it under:')).toBeNull();
  });
});
