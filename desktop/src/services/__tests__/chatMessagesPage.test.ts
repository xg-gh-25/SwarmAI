/**
 * chatService.getSessionMessagesPage — history pages in user turns.
 *
 * What is tested: the request asks for `turns` (and `before_id` when paging back);
 * hasMore comes from the X-Has-More header, NOT from the message count (the bug: a
 * 200-row page merged into 4 bubbles read as "no older history"); a 304 restores
 * the cached page AND its hasMore; an older page never sends If-None-Match.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

import api from '../api';
import { chatService } from '../chat';

const getMock = vi.spyOn(api, 'get');

const row = (id: string, role: string) => ({
  id, session_id: 's', role, content: [{ type: 'text', text: id }], created_at: '2026-10-22T00:00:00Z',
});

describe('chatService.getSessionMessagesPage', () => {
  beforeEach(() => {
    getMock.mockReset();
    chatService.invalidateMessageCache('s');
  });

  it('few merged messages but X-Has-More: 1 → hasMore true', async () => {
    getMock.mockResolvedValueOnce({
      status: 200, data: [row('u1', 'user'), row('a1', 'assistant')], headers: { etag: '"s:484:0.0:t20"', 'x-has-more': '1' },
    } as never);
    const page = await chatService.getSessionMessagesPage('s', 20);
    expect(getMock.mock.calls[0][0]).toBe('/chat/sessions/s/messages?turns=20');
    expect(page.messages.map((m) => m.id)).toEqual(['u1', 'a1']);
    expect(page.hasMore).toBe(true);
  });

  it('a 304 restores the cached page and its hasMore', async () => {
    getMock.mockResolvedValueOnce({
      status: 200, data: [row('u1', 'user')], headers: { etag: '"e1"', 'x-has-more': '1' },
    } as never);
    await chatService.getSessionMessagesPage('s', 20);
    getMock.mockResolvedValueOnce({ status: 304, data: undefined, headers: {} } as never);
    const again = await chatService.getSessionMessagesPage('s', 20);
    const sent = getMock.mock.calls[1][1] as { headers: Record<string, string> };
    expect(sent.headers['If-None-Match']).toBe('"e1"');
    expect(again).toEqual({ messages: [expect.objectContaining({ id: 'u1' })], hasMore: true });
  });

  it('an older page sends before_id, no If-None-Match; no header → hasMore false', async () => {
    getMock.mockResolvedValueOnce({ status: 200, data: [row('u0', 'user')], headers: { etag: '"e1"' } } as never);
    await chatService.getSessionMessagesPage('s', 20);
    getMock.mockResolvedValueOnce({ status: 200, data: [row('u-1', 'user')], headers: {} } as never);
    const older = await chatService.getSessionMessagesPage('s', 20, 'u0');
    expect(getMock.mock.calls[1][0]).toBe('/chat/sessions/s/messages?turns=20&before_id=u0');
    expect((getMock.mock.calls[1][1] as { headers: Record<string, string> }).headers['If-None-Match']).toBeUndefined();
    expect(older.hasMore).toBe(false);
  });
});
