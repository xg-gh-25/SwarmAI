/**
 * A chat tab's runs mark (design "the runs this session dispatched, visible in the
 * dispatching tab" r2 §3 ①).
 *
 * Questions: does a tab whose session has unsettled runs show the hexagon chip with
 * their number, amber exactly when one waits on the person; does a tab with none (or
 * only settled ones), the pinned Swarm Out tab and a Hive tab show nothing; does a click
 * on the chip open the Canvas Runs (and still select the tab); does the background
 * agents' ring stay where it was.
 */
import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { SessionTab } from './SessionTab';
import type { OpenTab } from '../types';

const tab: OpenTab = { id: 'tab-1', title: 'Fix things', agentId: 'a', isNew: false, sessionId: 'S1' } as OpenTab;
const base = { tab, isActive: false, onSelect: vi.fn(), onClose: vi.fn() };

describe('the runs mark', () => {
  it('shows the unsettled runs in the agent colour', () => {
    render(<SessionTab {...base} runsMark={{ runs: 3, live: 2, waits: false }} />);
    const mark = screen.getByTestId('tab-runs-mark');
    expect(mark.textContent).toBe('⬡2');
    expect(mark.dataset.waits).toBeUndefined();
    expect(mark.getAttribute('style')).toContain('--line-judge');
  });

  it('is amber when one waits on the person', () => {
    render(<SessionTab {...base} runsMark={{ runs: 1, live: 1, waits: true }} />);
    const mark = screen.getByTestId('tab-runs-mark');
    expect(mark.dataset.waits).toBe('true');
    expect(mark.getAttribute('style')).toContain('--color-status-warning');
  });

  it('shows nothing with no unsettled run, and never on the Swarm Out or a Hive tab', () => {
    const { rerender } = render(<SessionTab {...base} runsMark={{ runs: 2, live: 0, waits: false }} />);
    expect(screen.queryByTestId('tab-runs-mark')).toBeNull();
    rerender(<SessionTab {...base} runsMark={null} />);
    expect(screen.queryByTestId('tab-runs-mark')).toBeNull();
    rerender(<SessionTab {...base} variant="swarm-out" runsMark={{ runs: 1, live: 1, waits: true }} />);
    expect(screen.queryByTestId('tab-runs-mark')).toBeNull();
    rerender(<SessionTab {...base} variant="hive" runsMark={{ runs: 1, live: 1, waits: true }} />);
    expect(screen.queryByTestId('tab-runs-mark')).toBeNull();
  });

  it('a click opens the Canvas Runs and selects the tab', () => {
    const onOpenRuns = vi.fn();
    const onSelect = vi.fn();
    render(<SessionTab {...base} onSelect={onSelect} runsMark={{ runs: 1, live: 1, waits: false }} onOpenRuns={onOpenRuns} />);
    fireEvent.click(screen.getByTestId('tab-runs-mark'));
    expect(onOpenRuns).toHaveBeenCalledTimes(1);
    expect(onSelect).toHaveBeenCalledWith('tab-1');
  });

  it('Enter on the chip is the chip\'s, not the tab\'s', () => {
    const onSelect = vi.fn();
    render(<SessionTab {...base} onSelect={onSelect} runsMark={{ runs: 1, live: 1, waits: false }} onOpenRuns={vi.fn()} />);
    fireEvent.keyDown(screen.getByTestId('tab-runs-mark'), { key: 'Enter' });
    expect(onSelect).not.toHaveBeenCalled();
  });
});
