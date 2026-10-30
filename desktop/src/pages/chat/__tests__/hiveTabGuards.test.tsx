/**
 * What the Hive tab refuses to do on this machine, and what it changes in a run card
 * (design docs/design/2026-10-29-swarm-out-hive-session-tab.md §4, §6.4, §7).
 *
 * Questions: does a run title open the Hive tab only when the run has a session this
 * Host knows (R1.3); does an open-file for a Hive tab or a remote session open nothing
 * here (R4.1); does a Hive session's markdown keep its paths and workspace images off
 * this machine while local tabs are unchanged (R4.2); is an unreviewable command
 * deny-only in a Hive tab and approvable everywhere else (R5.1, R5.2); does a landing
 * while the Hive tab is active still find an empty user tab.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, renderHook, act } from '@testing-library/react';
import MarkdownRenderer, { OPEN_FILE_EVENT } from '../../../components/common/MarkdownRenderer';
import { RemoteContentContext } from '../remoteContentContext';
import { InlinePermissionRequest } from '../components/InlinePermissionRequest';
import { RunTitle } from '../../../components/swarm-out/LiveDispatches';
import { OpenSessionContext } from '../../../components/swarm-out/openSession';
import type { SwarmOutRun } from '../../../services/swarmOut';
import { useCanvasHost } from '../../../hooks/useCanvasHost';
import { hiveTabId, SWARM_OUT_TAB_ID } from '../swarmOut';
import { setRemote } from '../../../services/remoteSession';
import { classifyWorkLanding } from '../landPromptInTab';

vi.mock('../../../services/api', () => ({
  default: { get: vi.fn(async (_url: string, opts?: { params?: { path?: string } }) => ({ data: { resolved_path: opts?.params?.path ?? '' } })), post: vi.fn(), delete: vi.fn() },
}));
vi.mock('../../../services/canvasSession', () => ({
  refreshCanvasSession: async () => {},
  refreshCanvasArtifacts: async () => {},
  setCanvasStreaming: async () => {},
  setActiveCanvasSession: () => {},
  useCanvasSessionArtifacts: () => undefined,
  cachedArtifacts: () => undefined,
  sessionForTab: () => null,
}));

const toasts: string[] = [];
const onToast = (e: Event) => toasts.push((e as CustomEvent).detail?.message);
const opens: unknown[] = [];
const onOpen = (e: Event) => opens.push((e as CustomEvent).detail);
beforeEach(() => {
  toasts.length = 0; opens.length = 0; setRemote(null); sessionStorage.clear();
  document.addEventListener('swarm:toast', onToast);
});
afterEach(() => { document.removeEventListener('swarm:toast', onToast); document.removeEventListener(OPEN_FILE_EVENT, onOpen); });

const MD = 'see [the report](Projects/x/REPORT.md) and `src/app/main.ts` ![shot](Knowledge/a.png) [site](https://example.com)';

describe('markdown of a Hive session (R4.2)', () => {
  it('renders path links and path code as text, loads no image, keeps an external link', () => {
    document.addEventListener(OPEN_FILE_EVENT, onOpen);
    const { container } = render(<RemoteContentContext.Provider value><MarkdownRenderer content={MD} /></RemoteContentContext.Provider>);
    const pathText = screen.getByTestId('remote-path-text');
    expect(pathText.textContent).toBe('the report');
    expect(container.querySelector('a[href="Projects/x/REPORT.md"]')).toBeNull();
    fireEvent.click(pathText);
    fireEvent.click(screen.getByText('src/app/main.ts'));
    expect(opens).toHaveLength(0);
    expect(container.querySelector('img')).toBeNull();
    expect(screen.getByText('shot')).toBeTruthy();
    expect(screen.getByText('site').closest('a')).not.toBeNull();
  });

  it('a local tab renders the same markdown as before: a path link that opens, an image', () => {
    document.addEventListener(OPEN_FILE_EVENT, onOpen);
    const { container } = render(<MarkdownRenderer content={MD} />);
    expect(screen.queryByTestId('remote-path-text')).toBeNull();
    fireEvent.click(screen.getByText('the report'));
    fireEvent.click(screen.getByText('src/app/main.ts'));
    expect(opens).toHaveLength(2);
    expect(container.querySelector('img')).not.toBeNull();
  });
});

describe('approval in a Hive tab (R5.1, R5.2)', () => {
  const card = (command: string) => (
    <InlinePermissionRequest requestId="r1" toolName="Bash" toolInput={{ command }} reason="why" isPending onDecision={vi.fn()} />
  );

  it('an over-long or control-character command offers Deny only', () => {
    for (const cmd of ['x'.repeat(2001), 'echo \u001b[2Jok']) {
      const { unmount } = render(<RemoteContentContext.Provider value>{card(cmd)}</RemoteContentContext.Provider>);
      expect(screen.queryByText('Approve')).toBeNull();
      expect(screen.getByText('Deny')).toBeTruthy();
      expect(screen.getByTestId('permission-deny-only')).toBeTruthy();
      unmount();
    }
  });

  it('an ordinary command in a Hive tab can be approved', () => {
    render(<RemoteContentContext.Provider value>{card('ls -la')}</RemoteContentContext.Provider>);
    expect(screen.getByText('Approve')).toBeTruthy();
  });

  it('a local tab approves an over-long command as before', () => {
    render(card('x'.repeat(2001)));
    expect(screen.getByText('Approve')).toBeTruthy();
    expect(screen.queryByTestId('permission-deny-only')).toBeNull();
  });
});

describe('run titles (R1.3)', () => {
  const run = (over: Partial<SwarmOutRun> = {}) => ({
    dispatch_id: 'd1', title: 'Fix it', hive: 'hive-a', session_id: 'S1', sent_at: '2026-10-29T00:00:00Z', ...over,
  }) as unknown as SwarmOutRun;

  it('opens the run\'s session with the Hive and the run in the title', () => {
    const open = vi.fn();
    render(<OpenSessionContext.Provider value={open}><RunTitle r={run()} className="" /></OpenSessionContext.Provider>);
    fireEvent.click(screen.getByTestId('swarm-out-run-open'));
    expect(open).toHaveBeenCalledWith('S1', 'Fix it · hive-a');
  });

  it.each([
    ['no session', { session_id: null }],
    ['an unknown session', { session_unknown: true }],
    ['a session id the proxy would not accept', { session_id: 'run.1' }],
  ])('is plain text for a run with %s', (_n, over) => {
    render(<OpenSessionContext.Provider value={vi.fn()}><RunTitle r={run(over)} className="" /></OpenSessionContext.Provider>);
    expect(screen.queryByTestId('swarm-out-run-open')).toBeNull();
    expect(screen.getByText('Fix it')).toBeTruthy();
  });

  it('is plain text outside ChatPage (no opener)', () => {
    render(<RunTitle r={run()} className="" />);
    expect(screen.queryByTestId('swarm-out-run-open')).toBeNull();
  });
});

describe('open-file for the Hive tab (R4.1)', () => {
  const fire = (detail: Record<string, unknown>) =>
    act(async () => { document.dispatchEvent(new CustomEvent('swarm:open-file', { detail })); await Promise.resolve(); await Promise.resolve(); });

  it('opens nothing when the Hive tab is the landing tab', async () => {
    const api = (await import('../../../services/api')).default;
    vi.mocked(api.get).mockClear();
    setRemote('H');
    const { result } = renderHook(() => useCanvasHost({ activeTabId: hiveTabId('H'), sessionId: 'H', isStreaming: false }));
    await fire({ path: 'Knowledge/a.md' });
    expect(result.current.file).toBeNull();
    expect(api.get).not.toHaveBeenCalled();
    expect(toasts).toHaveLength(1);
  });

  it('opens nothing for an open that names the remote session, whatever tab it lands in', async () => {
    setRemote('H');
    const { result } = renderHook(() => useCanvasHost({ activeTabId: 'tab-A', sessionId: 'L', isStreaming: false }));
    await fire({ path: 'Knowledge/a.md', sessionId: 'H' });
    expect(result.current.file).toBeNull();
  });

  it('opens nothing for an open stamped with the Hive tab while another tab is active', async () => {
    setRemote('H');
    const { result } = renderHook(() => useCanvasHost({ activeTabId: 'tab-A', sessionId: 'L', isStreaming: false }));
    await fire({ path: 'Knowledge/a.md', tabId: hiveTabId('H') });
    expect(result.current.file).toBeNull();
    expect(toasts).toHaveLength(1);
  });

  it('a local tab still opens the file', async () => {
    setRemote('H');
    const { result } = renderHook(() => useCanvasHost({ activeTabId: 'tab-A', sessionId: 'L', isStreaming: false }));
    await fire({ path: 'Knowledge/a.md' });
    expect(result.current.file?.path ?? result.current.file).toBeTruthy();
    expect(toasts).toHaveLength(0);
  });
});

describe('landing while the Hive tab is active', () => {
  it('reuses an empty idle user tab at the cap, like on Swarm Out', () => {
    const tabs = [
      { id: 'a', sessionId: undefined, status: 'idle' as const, isStreaming: false },
      { id: SWARM_OUT_TAB_ID, sessionId: 'so', status: 'idle' as const, isStreaming: false },
      { id: hiveTabId('H'), sessionId: 'H', status: 'idle' as const, isStreaming: false },
    ];
    const v = classifyWorkLanding('k', tabs, 2, hiveTabId('H'), { hasDraft: false, applyDraftGuard: true, allowReuseCurrent: false });
    expect(v).toEqual({ kind: 'land', mode: 'reuse', tabId: 'a' });
  });
});
