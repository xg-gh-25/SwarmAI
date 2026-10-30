/**
 * The Swarm Out tab's welcome and its "new session" (SwarmOutWelcome,
 * SwarmOutNewSession, pages/chat/swarmOutFresh).
 *
 * Questions: does a starter put its sentence into the input without sending it; with
 * no running Hive, is the dispatch starter replaced by one notice that opens the Hive
 * card; is "new session" hidden on an empty session, impossible (with the reason)
 * while replying or while a question waits, highlighted when the session is long; does
 * pressing it change the session only when nothing is in flight, keep the old one
 * reachable, and clear the context reading.
 */
import { describe, it, expect, vi, beforeAll, afterAll, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import i18n from '../../i18n';
import { SwarmOutWelcome, canDispatch } from './SwarmOutWelcome';
import { SwarmOutNewSession, newSessionBlock } from './SwarmOutNewSession';
import { noticeLongSession, startSwarmOutFresh } from '../../pages/chat/swarmOutFresh';
import { tabNoticeStore } from '../../stores/tabNoticeStore';
import { INJECT_CHAT_INPUT } from '../../pages/chat/injectChatInput';
import { swarmOutService, type SwarmOutFleet } from '../../services/swarmOut';

beforeAll(async () => { await i18n.changeLanguage('zh'); });
afterAll(async () => { await i18n.changeLanguage('en'); });

vi.mock('../../services/swarmOut', () => ({ swarmOutService: { fleet: vi.fn(), runs: vi.fn() } }));
const fleet = vi.mocked(swarmOutService.fleet);

const hive = (status: string, reachable: boolean | null = status === 'running') => ({
  name: `h-${status}`, status, region: null, version: null, instance_type: null, reachable,
  streaming: 0, saturated: false, memory_percent: 5 });

beforeEach(() => { fleet.mockReset(); });

describe('SwarmOutWelcome', () => {
  it('a starter fills the input and sends nothing', async () => {
    fleet.mockResolvedValue({ running: 1, hives: [hive('running')] } as SwarmOutFleet);
    const got: unknown[] = [];
    const on = (e: Event) => got.push((e as CustomEvent).detail);
    window.addEventListener(INJECT_CHAT_INPUT, on);
    render(<SwarmOutWelcome />);
    fireEvent.click(await screen.findByTestId('swarm-out-start-dispatch'));
    fireEvent.click(screen.getByTestId('swarm-out-start-results'));
    window.removeEventListener(INJECT_CHAT_INPUT, on);
    expect(got).toEqual([{ text: '派一个任务到 Hive：', focus: true, append: true },
      { text: '最近的 run 结果怎么样，有没有要我处理的', focus: true, append: true }]);
  });

  it('with no running Hive, one notice replaces the dispatch starter and opens the Hive card', async () => {
    fleet.mockResolvedValue({ running: 0, hives: [hive('stopped'), hive('stopped')] } as SwarmOutFleet);
    const open = vi.fn();
    render(<SwarmOutWelcome onOpenHive={open} />);
    const notice = await screen.findByTestId('swarm-out-welcome-nohive');
    expect(notice.textContent).toContain('你有 2 台已停止的 Hive');
    expect(screen.queryByTestId('swarm-out-start-dispatch')).toBeNull();
    expect(screen.getByTestId('swarm-out-start-results')).toBeTruthy();       // needs no Hive
    fireEvent.click(screen.getByTestId('swarm-out-welcome-open-hive'));
    expect(open).toHaveBeenCalled();
  });

  it('canDispatch: unknown until read; a running Hive that does not answer does not count', () => {
    expect(canDispatch(null)).toBeNull();
    expect(canDispatch({ running: 1, hives: [hive('running', false)] } as SwarmOutFleet)).toBe(false);
    expect(canDispatch({ running: 1, hives: [hive('running', true)] } as SwarmOutFleet)).toBe(true);
  });
});

describe('SwarmOutNewSession', () => {
  it('is not shown on an empty session', () => {
    const { container } = render(<SwarmOutNewSession hasContent={false} block={null} long={false} onStart={() => {}} />);
    expect(container.textContent).toBe('');
  });

  it('cannot be pressed while replying or while a question waits, and says why', () => {
    const start = vi.fn();
    const { rerender } = render(<SwarmOutNewSession hasContent block="replying" long={false} onStart={start} />);
    const b = screen.getByTestId('swarm-out-new-session');
    expect(b.getAttribute('aria-disabled')).toBe('true');
    expect(b.getAttribute('title')).toContain('正在回复');
    fireEvent.click(b);
    rerender(<SwarmOutNewSession hasContent block="waiting" long={false} onStart={start} />);
    expect(screen.getByTestId('swarm-out-new-session').getAttribute('title')).toContain('确认卡片在等你');
    fireEvent.click(screen.getByTestId('swarm-out-new-session'));
    expect(start).not.toHaveBeenCalled();
  });

  it('is highlighted when the session is long, and starts on a press', () => {
    const start = vi.fn();
    render(<SwarmOutNewSession hasContent block={null} long onStart={start} />);
    const b = screen.getByTestId('swarm-out-new-session');
    expect(b.getAttribute('data-long')).toBe('true');
    fireEvent.click(b);
    expect(start).toHaveBeenCalledTimes(1);
  });

  it('newSessionBlock reads the tab', () => {
    expect(newSessionBlock({ isStreaming: true })).toBe('replying');
    expect(newSessionBlock({ pendingQuestion: { id: 'q' } })).toBe('waiting');
    expect(newSessionBlock({ pendingPermissionRequestId: 'p' })).toBe('waiting');
    expect(newSessionBlock({ _serverQueue: [{}] })).toBe('replying');        // a reset would drop it
    expect(newSessionBlock({ _queuedLocal: new Map([['c1', undefined]]) })).toBe('replying');
    expect(newSessionBlock({})).toBeNull();
  });
});

describe('startSwarmOutFresh', () => {
  function deps(tab: Record<string, unknown> | undefined, liveBlock: 'replying' | 'waiting' | null = null) {
    const state = { tab: tab ? { id: 'swarm-out', ...tab } : undefined };
    return {
      state,
      d: {
        getTab: () => state.tab as never,
        liveBlock,
        reset: vi.fn(() => { if (state.tab) state.tab = { id: 'swarm-out', sessionId: undefined }; }),
        clearContext: vi.fn(),
        openOld: vi.fn(),
        t: (k: string) => k,
      },
    };
  }

  it('changes nothing while a turn runs or a question waits', () => {
    for (const [tab, live] of [[{ sessionId: 's1', isStreaming: true }, null], [{ sessionId: 's1' }, 'waiting']] as const) {
      const { d } = deps(tab, live);
      expect(startSwarmOutFresh(d)).toBe(false);
      expect(d.reset).not.toHaveBeenCalled();
    }
  });

  it('starts a new session, clears the context reading, and offers the old one', async () => {
    const { d } = deps({ sessionId: 's-old' });
    expect(startSwarmOutFresh(d)).toBe(true);
    expect(d.reset).toHaveBeenCalledWith('swarm-out');
    expect(d.clearContext).toHaveBeenCalled();
    const n = tabNoticeStore.getSnapshot('swarm-out').find((x) => x.id === 'swarm-out-fresh');
    expect(n?.message).toBe('swarmOut.fresh.done');
    n?.action?.run('swarm-out', undefined);
    await waitFor(() => expect(d.openOld).toHaveBeenCalledWith('s-old'));
  });
});

describe('noticeLongSession', () => {
  it('tells each long session once, from the warning level', () => {
    const seen = new Set<string>();
    const t = (k: string) => k;
    const tab = { id: 'swarm-out-long-test', sessionId: 's1' };
    expect(noticeLongSession(tab, 'ok', 40, seen, t)).toBe(false);
    expect(noticeLongSession(tab, 'warn', 72, seen, t)).toBe(true);
    expect(tabNoticeStore.getSnapshot('swarm-out-long-test').map((n) => n.id)).toEqual(['swarm-out-long']);
    expect(noticeLongSession(tab, 'critical', 86, seen, t)).toBe(false);
    expect(noticeLongSession({ id: 'swarm-out-long-test', sessionId: 's2' }, 'warn', 70, seen, t)).toBe(true);
    expect(noticeLongSession({ id: 'x', sessionId: undefined }, 'warn', 70, seen, t)).toBe(false);
  });
});
