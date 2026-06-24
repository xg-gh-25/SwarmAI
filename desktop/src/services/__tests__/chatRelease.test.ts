/**
 * Contract tests for chatService.releaseSession (R6b — tab-close slot release).
 *
 * Locks the wire format the backend parses (routers/chat.py release endpoint):
 * - Idle close → POST /chat/release/{id} with NO body (unforced).
 * - Streaming confirmed close → POST /chat/release/{id} with { force: true }.
 *
 * The force flag is the AC1/AC2 discriminator: an unforced release leaves an
 * active session untouched server-side; force routes it through the
 * generation-safe interrupt path. A wrong body shape silently breaks that.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../api', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}));

import api from '../api';
import { chatService } from '../chat';

describe('chatService.releaseSession (R6b)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('idle close: posts to /chat/release/{id} with NO body (unforced)', async () => {
    vi.mocked(api.post).mockResolvedValue({ data: { status: 'released', alive_count: 0 } });

    const result = await chatService.releaseSession('sess-A');

    expect(api.post).toHaveBeenCalledWith('/chat/release/sess-A', undefined);
    expect(result.status).toBe('released');
  });

  it('idle close (explicit force=false): still sends NO body', async () => {
    vi.mocked(api.post).mockResolvedValue({ data: { status: 'released' } });

    await chatService.releaseSession('sess-A', false);

    expect(api.post).toHaveBeenCalledWith('/chat/release/sess-A', undefined);
  });

  it('streaming confirmed close: posts { force: true }', async () => {
    vi.mocked(api.post).mockResolvedValue({ data: { status: 'released', alive_count: 0 } });

    await chatService.releaseSession('sess-S', true);

    expect(api.post).toHaveBeenCalledWith('/chat/release/sess-S', { force: true });
  });

  it('returns the server status payload (released | skipped_active | not_found)', async () => {
    vi.mocked(api.post).mockResolvedValue({ data: { status: 'skipped_active', alive_count: 2 } });

    const result = await chatService.releaseSession('sess-S');

    expect(result.status).toBe('skipped_active');
    expect(result.alive_count).toBe(2);
  });
});
