/**
 * A Hive tab's stream never touches this machine's Canvas or drives this app's UI
 * (design docs/design/2026-10-29-swarm-out-hive-session-tab.md §7, R4.3). The Host
 * relay already drops file_changed and ui_command; this is the frontend's own wall.
 *
 * Questions: does a file_changed or ui_command on a Hive tab's stream do nothing,
 * while the same events on a local tab still do what they did.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';

const dispatchUiCommand = vi.hoisted(() => vi.fn());
vi.mock('../utils/uiCommands', async (orig) => ({ ...(await orig<typeof import('../utils/uiCommands')>()), dispatchUiCommand }));
vi.mock('../contexts/ToastContext', () => ({ useToast: () => ({ addToast: vi.fn(), removeToast: vi.fn(), toasts: [] }) }));

import { useChatStreamingLifecycle } from '../hooks/useChatStreamingLifecycle';
import { createMockDeps, initTestTab, resetTestState } from './helpers/streamingTestUtils';
import { hiveTabId } from '../pages/chat/swarmOut';
import type { StreamEvent } from '../types';

const changed: unknown[] = [];
const onChanged = (e: Event) => changed.push((e as CustomEvent).detail);

beforeEach(() => {
  resetTestState();
  dispatchUiCommand.mockClear();
  changed.length = 0;
  window.removeEventListener('swarm:file-changed', onChanged);
  window.addEventListener('swarm:file-changed', onChanged);
});

function fire(tabId: string, ev: Record<string, unknown>) {
  initTestTab(tabId);
  const { result } = renderHook(() => useChatStreamingLifecycle(createMockDeps()));
  const handler = result.current.createStreamHandler('m1', tabId);
  act(() => { handler(ev as unknown as StreamEvent); });
}

describe('stream events on a Hive tab', () => {
  it('a file_changed does nothing on the Hive tab, and still notifies on a local tab', () => {
    fire(hiveTabId('H'), { type: 'file_changed', path: 'Knowledge/a.md', relevance: 'deliverable' });
    expect(changed).toHaveLength(0);
    fire('tab-local', { type: 'file_changed', path: 'Knowledge/a.md', relevance: 'deliverable' });
    expect(changed).toHaveLength(1);
  });

  it('a ui_command does nothing on the Hive tab, and still runs on a local tab', () => {
    fire(hiveTabId('H'), { type: 'ui_command', cmd: 'open-canvas-file', path: 'Knowledge/a.md' });
    expect(dispatchUiCommand).not.toHaveBeenCalled();
    fire('tab-local', { type: 'ui_command', cmd: 'open-canvas-file', path: 'Knowledge/a.md' });
    expect(dispatchUiCommand).toHaveBeenCalledTimes(1);
  });
});
