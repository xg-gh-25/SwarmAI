/**
 * A Swarm Out item in Need You opens the Swarm Out tab, never a chat prompt (design
 * docs/design/2026-10-31-swarm-out-run-review.md §3.7); every other item still fills
 * or sends its prompt.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { NeedsYouRow } from './NeedsYouRow';
import { setOverlayCtxBridge } from './overlayRegistry';
import type { AttentionEntry } from '../../services/attention';

const item = (over: Partial<AttentionEntry> = {}): AttentionEntry => ({
  id: 'swarm_out:decide', source: 'swarm_out', tier: 'review', brain: null,
  title: '2 Swarm Out runs to accept or decide', summary: '2 in Swarm Out', detail: '',
  dispatch: { message: 'Open Swarm Out: 2 swarm out runs to accept or decide.', context: { kind: 'swarm_out', focus: 'decide' } },
  ...over,
});

afterEach(() => { cleanup(); setOverlayCtxBridge({}); });

describe('NeedsYouRow', () => {
  it('a Swarm Out item is one button that opens the Swarm Out tab and fills nothing', () => {
    const show = vi.fn();
    setOverlayCtxBridge({ showSwarmOut: show });
    const onFill = vi.fn(); const onSend = vi.fn(); const onOpened = vi.fn();
    render(<NeedsYouRow item={item()} onFill={onFill} onSend={onSend} onOpened={onOpened} />);
    expect(screen.queryByTestId('needs-you-row-send')).toBeNull();
    fireEvent.click(screen.getByTestId('needs-you-row-fill'));
    expect(show).toHaveBeenCalledTimes(1);
    expect(onOpened).toHaveBeenCalledTimes(1);
    expect(onFill).not.toHaveBeenCalled();
  });

  it('where the Swarm Out tab cannot be reached, it is an ordinary prompt', () => {
    const onFill = vi.fn();
    render(<NeedsYouRow item={item()} onFill={onFill} onSend={vi.fn()} />);
    fireEvent.click(screen.getByTestId('needs-you-row-fill'));
    expect(onFill).toHaveBeenCalledWith('Open Swarm Out: 2 swarm out runs to accept or decide.');
  });

  it('any other item fills or sends its prompt', () => {
    const show = vi.fn();
    setOverlayCtxBridge({ showSwarmOut: show });
    const onFill = vi.fn(); const onSend = vi.fn();
    const other = item({ source: 'job', dispatch: { message: 'Look at the job', context: { kind: 'job' } } });
    render(<NeedsYouRow item={other} onFill={onFill} onSend={onSend} />);
    fireEvent.click(screen.getByTestId('needs-you-row-fill'));
    fireEvent.click(screen.getByTestId('needs-you-row-send'));
    expect(onFill).toHaveBeenCalledWith('Look at the job');
    expect(onSend).toHaveBeenCalledWith('Look at the job');
    expect(show).not.toHaveBeenCalled();
  });
});

describe('NeedsYouRow labels follow the interface language', () => {
  it('the source label and the send button read in Chinese when the interface is Chinese', async () => {
    const i18n = (await import('../../i18n')).default;
    await i18n.changeLanguage('zh');
    try {
      render(<NeedsYouRow item={item({ id: 'governance:GC1', source: 'governance', dispatch: { message: 'm' } })}
        onFill={vi.fn()} onSend={vi.fn()} />);
      expect(screen.getByText('规则提议')).toBeTruthy();
      expect(screen.getByTestId('needs-you-row-send').getAttribute('title')).toBe('直接发送');
    } finally {
      await i18n.changeLanguage('en');
    }
  });

  it('every backend source has a label in both languages', async () => {
    const en = (await import('../../i18n/locales/en.json')).default as { needYou: { source: Record<string, string> } };
    const zh = (await import('../../i18n/locales/zh.json')).default as { needYou: { source: Record<string, string> } };
    const sources: AttentionEntry['source'][] = ['escalation', 'paused_run', 'governance', 'job', 'job_digest',
      'db_recovery', 'ingestion', 'local_root', 'swarm_out', 'todo'];
    for (const s of sources) {
      expect(en.needYou.source[s]).toBeTruthy();
      expect(zh.needYou.source[s]).toBeTruthy();
    }
  });
});
