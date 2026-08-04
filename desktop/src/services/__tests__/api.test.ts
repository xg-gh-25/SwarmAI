/**
 * Unit tests for classifyLoadError — the shared 4xx-vs-outage classifier extracted
 * from ToDoOverlay and now wired into Pipeline/Pollinate/CMBrain overlays.
 *
 * The load-bearing behaviors:
 *  - a 4xx (ApiError, statusCode < 500) → CLIENT-error message (not "backend unavailable")
 *  - a 5xx / network-outage (statusCode >= 500, incl. the no-response 500 fallback) → outage
 *  - a non-ApiError throw → conservative outage (never misrouted to client-error)
 *  - the optional outageMsg override replaces ONLY the outage branch (CMBrain nuance)
 *  - console.error fires ONLY on the 4xx branch
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { classifyLoadError, ApiError } from '../api';

afterEach(() => vi.restoreAllMocks());

describe('classifyLoadError', () => {
  it('4xx → client-error message with HTTP code, NOT backend-unavailable', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    const msg = classifyLoadError(new ApiError({ code: 'VALIDATION_FAILED', message: 'bad' }, 400), 'ToDos');
    expect(msg).toContain('HTTP 400');
    expect(msg).toContain('client error');
    expect(msg).not.toContain('backend may be unavailable');
    expect(spy).toHaveBeenCalledOnce(); // 4xx is logged (backend is up)
  });

  it('5xx → outage message, no client-error wording', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    const msg = classifyLoadError(new ApiError({ code: 'SERVICE_UNAVAILABLE', message: 'down' }, 503), 'pipeline analytics');
    expect(msg).toContain('backend may be unavailable');
    expect(msg).not.toContain('client error');
    expect(spy).not.toHaveBeenCalled(); // outage branch does not log
  });

  it('network outage (no-response → statusCode 500 fallback) stays outage', () => {
    const msg = classifyLoadError(new ApiError({ code: 'SERVICE_UNAVAILABLE', message: 'unavailable' }, 500), 'content assets');
    expect(msg).toContain('backend may be unavailable');
    expect(msg).not.toContain('client error');
  });

  it('non-ApiError throw defaults to outage (never client-error)', () => {
    expect(classifyLoadError(new Error('boom'), 'X')).toContain('backend may be unavailable');
    expect(classifyLoadError(null, 'X')).toContain('backend may be unavailable');
    expect(classifyLoadError(undefined, 'X')).not.toContain('client error');
  });

  it('outageMsg override replaces ONLY the outage branch (CMBrain nuance preserved)', () => {
    const nuance = 'Couldn’t load the queue — the backend may be unavailable. This is NOT “nothing to do”.';
    // outage path uses the override verbatim…
    expect(classifyLoadError(new ApiError({ code: 'X', message: 'y' }, 503), 'the queue', nuance)).toBe(nuance);
    expect(classifyLoadError(new Error('net'), 'the queue', nuance)).toBe(nuance);
    // …but a 4xx still routes to the standard client-error message, NOT the override.
    vi.spyOn(console, 'error').mockImplementation(() => {});
    const client = classifyLoadError(new ApiError({ code: 'X', message: 'y' }, 404), 'the queue', nuance);
    expect(client).toContain('HTTP 404');
    expect(client).toContain('client error');
    expect(client).not.toBe(nuance);
  });

  it('embeds the {what} noun in both branches', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    expect(classifyLoadError(new ApiError({ code: 'X', message: 'y' }, 400), 'content assets')).toContain('content assets');
    expect(classifyLoadError(new Error('x'), 'content assets')).toContain('content assets');
  });
});
