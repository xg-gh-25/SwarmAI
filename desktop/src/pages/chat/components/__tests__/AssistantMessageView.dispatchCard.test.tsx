/**
 * The Swarm Out dispatch card in the turn (design
 * docs/design/2026-10-30-swarm-out-dispatch-card.md §3), through the real
 * AssistantMessageView:
 * - a `swarm_out_dispatch` block is a first-level zone after the call that dispatched,
 *   and it ends that work like products do; a "know" zone;
 * - gone out: title, brief, every field with its reason, the uncommitted warning, the
 *   dispatch id; "Open Hive session" opens that session in the Hive tab, "View in Swarm
 *   Out" selects the Swarm Out tab; the live state comes from the runs poll, one poll
 *   for every card on screen;
 * - refused: the reason, "nothing was sent", no session to open, "Open <Brain>";
 * - not sure (no answer in time): "may already be running", never "nothing was sent".
 */
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { AssistantMessageView } from '../AssistantMessageView';
import { ToastProvider } from '../../../../contexts/ToastContext';
import { setOverlayCtxBridge } from '../../../../components/layout/overlayRegistry';
import { swarmOutService } from '../../../../services/swarmOut';
import { _resetDispatchPoll } from '../DispatchCard';
import type { Message, ContentBlock, SwarmOutDispatchContent } from '../../../../types';

vi.mock('react-i18next', () => ({
  initReactI18next: { type: '3rdParty', init: () => {} },
  useTranslation: () => ({
    t: (_key: string, o?: { defaultValue?: string } & Record<string, unknown>) =>
      String(o?.defaultValue ?? _key).replace(/\{\{(\w+)\}\}/g, (_m, v) => String(o?.[v] ?? '')),
  }),
}));
vi.mock('../../../../contexts/HealthContext', () => ({
  useHealth: () => ({ health: { status: 'connected', lastCheckedAt: null, consecutiveFailures: 0 }, triggerHealthCheck: vi.fn() }),
}));

const SID = '0a7e6d08-f227-4795-b07d-75eb60f64450';
const bash = (id: string): ContentBlock =>
  ({ type: 'tool_use', id, name: 'Bash', summary: 'Running: swarm_out dispatch', category: 'bash' }) as ContentBlock;
const tr = (id: string): ContentBlock => ({ type: 'tool_result', toolUseId: id, content: 'ok', isError: false, truncated: false }) as ContentBlock;
const tx = (text: string): ContentBlock => ({ type: 'text', text }) as ContentBlock;

const OK: SwarmOutDispatchContent = {
  type: 'swarm_out_dispatch', ok: true, maybeSent: false, dispatchId: 'so_b150abc3fd86', sessionId: SID,
  title: 'SwarmAI weekly report', brief: 'Read the week of commits and write what changed.',
  hive: 'explorer', hiveReason: 'least_busy:1', brain: 'SwarmAI', mode: 'research', profile: 'research',
  revision: 'fcdbf4dc' + '0'.repeat(56), brainSync: 'synced',
  repos: [{ name: 'swarmai', ref: 'main', sha: 'a680d1a0' + '0'.repeat(32), dirty: 3 }],
  acceptance: 'a report exists', delivery: 'bundle', error: null, at: '2026-10-30T05:25:50Z', text: 'x',
};
const REFUSED: SwarmOutDispatchContent = {
  ...OK, ok: false, dispatchId: null, sessionId: null, brief: null, hiveReason: null, hive: null, mode: null,
  profile: null, revision: null, brainSync: null, repos: [], acceptance: null, delivery: null,
  error: 'SwarmAI cannot be sent to a Hive: the definition was changed',
};

function view(content: ContentBlock[], isStreaming = false, id = 'm1') {
  const message: Message = { id, role: 'assistant', content, timestamp: '2026-10-30T13:25:00+08:00', isError: false };
  return <ToastProvider><AssistantMessageView message={message} isStreaming={isStreaming} isLastAssistant sessionId="s1" /></ToastProvider>;
}
const zones = (root: HTMLElement) => [...root.querySelectorAll('[data-zone]')].map((e) => e.getAttribute('data-zone'));

let runs: ReturnType<typeof vi.spyOn>;
const openHiveSession = vi.fn();
const showSwarmOut = vi.fn();
beforeEach(() => {
  _resetDispatchPoll();
  runs = vi.spyOn(swarmOutService, 'runs').mockResolvedValue({
    runs: [{ dispatch_id: 'so_b150abc3fd86', hive_id: 'h1', hive: 'explorer', session_id: SID, title: 't', origin: 'swarm-out',
      sent_at: '2026-10-30T05:25:50Z', waiting: false, running: true, state: 'running', state_reason: 'streaming' }],
    asks: [], errors: [],
  });
  setOverlayCtxBridge({ openHiveSession, showSwarmOut });
});
afterEach(() => { vi.restoreAllMocks(); openHiveSession.mockReset(); showSwarmOut.mockReset(); setOverlayCtxBridge({}); });

describe('a dispatch that went out', () => {
  it('is a first-level card after the call, ending its work', () => {
    const { container } = render(view([bash('b1'), tr('b1'), OK, bash('b2')], true));
    expect(zones(container)).toEqual(['phase', 'dispatch', 'phase']);
    expect(screen.getByTestId('zone-dispatch').closest('[data-zone]')!.getAttribute('data-attn')).toBe('know');
  });

  it('shows what was sent, and the run state from the poll', async () => {
    render(view([bash('b1'), tr('b1'), OK, tx('Sent it to explorer.')]));
    const card = screen.getByTestId('dispatch-card');
    expect(card.getAttribute('data-ok')).toBe('true');
    expect(screen.getByTestId('dispatch-title').textContent).toBe('SwarmAI weekly report');
    expect(screen.getByTestId('dispatch-brief').textContent).toContain('Read the week');
    expect(screen.getByTestId('dispatch-row-hive').textContent).toContain('explorer');
    expect(screen.getByTestId('dispatch-row-hive').textContent).toContain('the least busy (1 running)');
    expect(screen.getByTestId('dispatch-row-agent').textContent).toContain('profile research');
    expect(screen.getByTestId('dispatch-row-agent').textContent).toContain('fcdbf4dc');
    expect(screen.getByTestId('dispatch-row-agent').textContent).toContain('Brain synced');
    expect(screen.getByTestId('dispatch-row-repo').textContent).toContain('swarmai @ main a680d1a0');
    expect(screen.getByTestId('dispatch-dirty').textContent).toContain('3 uncommitted');
    expect(card.textContent).toContain('so_b150abc3fd86');
    expect(card.textContent).not.toContain('Nothing was sent');
    const live = await screen.findByTestId('dispatch-live');
    expect(live.getAttribute('data-tone')).toBe('busy');
  });

  it('opens its Hive session, and Swarm Out', () => {
    render(view([bash('b1'), tr('b1'), OK]));
    fireEvent.click(screen.getByTestId('dispatch-open'));
    expect(openHiveSession).toHaveBeenCalledWith(SID, 'SwarmAI weekly report · explorer');
    fireEvent.click(screen.getByTestId('dispatch-view'));
    expect(showSwarmOut).toHaveBeenCalledTimes(1);
  });

  it('every card on screen shares one runs poll', async () => {
    render(<>{view([bash('b1'), tr('b1'), OK], false, 'm1')}{view([bash('b2'), tr('b2'), { ...OK, dispatchId: 'so_000000000001' }], false, 'm2')}</>);
    await screen.findByTestId('dispatch-live');
    expect(runs).toHaveBeenCalledTimes(1);
  });

  it('a session the Host cannot open has no Open button', () => {
    render(view([bash('b1'), tr('b1'), { ...OK, sessionId: null }]));
    expect(screen.queryByTestId('dispatch-open')).toBeNull();
  });
});

describe('a dispatch that did not go out', () => {
  it('says why and that nothing was sent; opens the Brain', () => {
    const shown: unknown[] = [];
    const on = (e: Event) => shown.push((e as CustomEvent).detail);
    window.addEventListener('swarm:show-brain-hub', on);
    render(view([bash('b1'), tr('b1'), REFUSED]));
    window.removeEventListener('swarm:show-brain-hub', on);
    expect(screen.getByTestId('dispatch-card').getAttribute('data-ok')).toBe('false');
    expect(screen.getByTestId('dispatch-error').textContent).toContain('definition was changed');
    expect(screen.getByTestId('dispatch-error').textContent).toContain('Nothing was sent');
    expect(screen.queryByTestId('dispatch-open')).toBeNull();
    expect(screen.queryByTestId('dispatch-live')).toBeNull();
    expect(runs).not.toHaveBeenCalled();
    window.addEventListener('swarm:show-brain-hub', on);
    fireEvent.click(screen.getByTestId('dispatch-open-brain'));
    window.removeEventListener('swarm:show-brain-hub', on);
    expect(shown).toEqual([{ brain: 'SwarmAI' }]);
  });

  it('one the Hive may have taken says so, never "nothing was sent"', async () => {
    render(view([bash('b1'), tr('b1'), { ...REFUSED, maybeSent: true, error: 'the task may be running there' }]));
    const card = screen.getByTestId('dispatch-card');
    expect(card.getAttribute('data-unsure')).toBe('true');
    expect(card.textContent).toContain('may already be running');
    expect(card.textContent).not.toContain('Nothing was sent');
    expect(screen.queryByTestId('dispatch-open-brain')).toBeNull();
    fireEvent.click(screen.getByTestId('dispatch-view'));
    await waitFor(() => expect(showSwarmOut).toHaveBeenCalled());
  });
});
