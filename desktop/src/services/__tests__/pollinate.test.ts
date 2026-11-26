/**
 * pollinate client: fetchWorkView turns the 404 of a work that is no longer registered
 * into null (the card says it is gone), and rethrows every other error (the card offers
 * a retry). The api interceptor rejects with ApiError, so that is what is thrown here.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

const get = vi.fn();
vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return { ...actual, default: { get: (...a: unknown[]) => get(...a) } };
});

import { ApiError } from '../api';
import { pollinateService } from '../pollinate';

describe('pollinateService.fetchWorkView', () => {
  beforeEach(() => { get.mockReset(); });

  it('a 404 is null', async () => {
    get.mockRejectedValue(new ApiError({ code: 'NOT_FOUND', message: 'not a registered work' }, 404));
    await expect(pollinateService.fetchWorkView('Projects/P/assets/gone')).resolves.toBeNull();
    expect(get).toHaveBeenCalledWith('/pollinate/works/view', { params: { path: 'Projects/P/assets/gone' } });
  });

  it('any other error is thrown', async () => {
    const err = new ApiError({ code: 'SERVICE_UNAVAILABLE', message: 'down' }, 500);
    get.mockRejectedValue(err);
    await expect(pollinateService.fetchWorkView('x')).rejects.toBe(err);
  });

  it('a body is read into the camelCase view', async () => {
    get.mockResolvedValue({ data: { work: { path: 'p', in_workspace: true, kind: 'deck', title: 'T', brain: 'B', versions: [] },
      actions: [{ key: 'open_canvas', primary: true, path: 'p/out/deck.html' }, { key: 'rm_rf', primary: false }] } });
    const v = await pollinateService.fetchWorkView('p');
    expect(v?.work.title).toBe('T');
    expect(v?.actions.map((a) => a.key)).toEqual(['open_canvas']);   // unknown keys are dropped
  });
});
