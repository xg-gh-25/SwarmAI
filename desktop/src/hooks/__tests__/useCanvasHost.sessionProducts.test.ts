/**
 * A user's click on a file reference resolves against the session's own products
 * first (design docs/design/2026-10-26-session-products.md §3.4).
 *
 * - the resolve call carries the tab's session id;
 * - a product answer keeps that session id on the opened CanvasFile, so its later
 *   reads pass the per-session render gate; a non-product answer adds nothing;
 * - a 409 (several products match) opens nothing and shows a toast naming them.
 *
 * Drives the real hook; api.get is mocked.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { Mock } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useCanvasHost } from '../useCanvasHost';
import api from '../../services/api';
import { resetCanvasSession } from '../../services/canvasSession';
import { installFakeCanvasBackend } from '../../test-utils/fakeCanvasBackend';

vi.mock('../../services/api', () => ({ default: { get: vi.fn(), post: vi.fn(), put: vi.fn() } }));

let fake: ReturnType<typeof installFakeCanvasBackend>;
let resolveAnswer: (params: Record<string, unknown>) => Promise<unknown>;
const calls: Record<string, unknown>[] = [];

beforeEach(() => {
  vi.clearAllMocks(); resetCanvasSession(); calls.length = 0;
  fake = installFakeCanvasBackend(api.get as Mock);
  const get = api.get as Mock;
  const prev = get.getMockImplementation()!;
  get.mockImplementation((url: string, cfg: { params?: Record<string, unknown> }) => {
    if (url !== '/workspace/file/resolve') return prev(url, cfg);
    calls.push(cfg?.params ?? {});
    return resolveAnswer(cfg?.params ?? {});
  });
});
afterEach(() => fake.uninstall());

const mount = () => renderHook(() => useCanvasHost({ activeTabId: 'A', sessionId: 's-A', isStreaming: false }));
const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });
const click = (path: string) => act(() => { document.dispatchEvent(new CustomEvent('swarm:open-file', { detail: { path } })); });

describe('resolve against the session products', () => {
  it('sends the tab session id and keeps it on a product it opened', async () => {
    resolveAnswer = async () => ({ data: { resolved_path: '/Users/u/Desktop/q3.md', session_product: true } });
    const { result } = mount();
    click('q3.md'); await flush();
    expect(calls[0]).toEqual({ path: 'q3.md', session_id: 's-A' });
    expect(result.current.file?.filePath).toBe('/Users/u/Desktop/q3.md');
    expect(result.current.file?.sessionId).toBe('s-A');
  });

  it('adds no session id when the answer is not a session product', async () => {
    resolveAnswer = async (p) => ({ data: { resolved_path: p.path } });
    const { result } = mount();
    click('Knowledge/a.md'); await flush();
    expect(result.current.file?.filePath).toBe('Knowledge/a.md');
    expect(result.current.file?.sessionId).toBeUndefined();
  });

  it('a 409 opens nothing and names the candidates in a toast', async () => {
    resolveAnswer = async () => Promise.reject({ response: { status: 409, data: { detail: { candidates: ['/a/plan.md', '/b/plan.md'] } } } });
    const toasts: string[] = [];
    const onToast = (e: Event) => toasts.push((e as CustomEvent<{ message: string }>).detail.message);
    document.addEventListener('swarm:toast', onToast);
    const { result } = mount();
    click('plan.md'); await flush();
    document.removeEventListener('swarm:toast', onToast);
    expect(result.current.file).toBeNull();
    expect(toasts.join(' ')).toContain('/a/plan.md');
    expect(toasts.join(' ')).toContain('/b/plan.md');
  });
});
