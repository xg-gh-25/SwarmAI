import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ChatHeader } from './ChatHeader';
import { HealthContext, type HealthContextValue } from '../../../contexts/HealthContext';
import type { OpenTab } from '../types';

// ChatHeader uses useTranslation (tail "+" labels + health warnings).
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_key: string, fallback?: string) => fallback ?? _key }),
}));

/**
 * ChatHeader — the 🔔 Alerts "Needs You" pill was RELOCATED to the left-sidebar
 * top slot (run_2bdc68ad). ChatHeader must no longer render it, and its props
 * interface no longer accepts the attention wiring (tsc enforces the latter —
 * the render assertions here enforce the former).
 */
function makeHealth(status: HealthContextValue['health']['status']): HealthContextValue {
  return {
    health: { status } as HealthContextValue['health'],
    triggerHealthCheck: vi.fn(),
  };
}

function renderHeader(
  healthStatus: HealthContextValue['health']['status'] = 'connected',
  extra: { outputCount?: number; lastSeenOutputCount?: number; canvasOpen?: boolean; onOpenCanvas?: () => void } = {},
) {
  const tabs: OpenTab[] = [
    { id: 'tab-0', title: 'One', agentId: 'a1', isNew: false },
  ];
  return render(
    <HealthContext.Provider value={makeHealth(healthStatus)}>
      <ChatHeader
        openTabs={tabs}
        activeTabId="tab-0"
        onTabSelect={vi.fn()}
        onTabClose={vi.fn()}
        onNewSession={vi.fn()}
        outputCount={extra.outputCount}
        lastSeenOutputCount={extra.lastSeenOutputCount}
        canvasOpen={extra.canvasOpen}
        onOpenCanvas={extra.onOpenCanvas}
      />
    </HealthContext.Provider>,
  );
}

describe('ChatHeader — Alerts pill relocated to sidebar (run_2bdc68ad)', () => {
  it('does NOT render the 🔔 "Needs You" Alerts pill (moved to left sidebar)', () => {
    renderHeader();
    expect(screen.queryByText('Needs You')).toBeNull();
    expect(screen.queryByRole('button', { name: /Alerts/i })).toBeNull();
  });

  it('still renders the tab strip with the tail "+" new-session button', () => {
    renderHeader();
    // The tablist (tabs) + the tail "+" (New Session) both present.
    expect(screen.getByRole('tablist')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /New Session/i })).toBeInTheDocument();
  });

  it('keeps the health warning in the right cluster when disconnected', () => {
    renderHeader('disconnected');
    expect(screen.getByLabelText(/Backend Offline/i)).toBeInTheDocument();
  });
});

describe('ChatHeader — Canvas outputs pill (run_9e42c066)', () => {
  it('shows the "N outputs" pill when outputCount>0 AND Canvas is CLOSED', () => {
    renderHeader('connected', { outputCount: 3, canvasOpen: false });
    const pill = screen.getByTestId('chat-header-outputs-pill');
    expect(pill).toBeInTheDocument();
    expect(pill).toHaveTextContent('3 outputs');
  });

  it('does NOT show the pill when Canvas is OPEN (rail is already visible)', () => {
    renderHeader('connected', { outputCount: 3, canvasOpen: true });
    expect(screen.queryByTestId('chat-header-outputs-pill')).toBeNull();
  });

  it('does NOT show the pill when there are zero outputs', () => {
    renderHeader('connected', { outputCount: 0, canvasOpen: false });
    expect(screen.queryByTestId('chat-header-outputs-pill')).toBeNull();
  });

  it('singularizes the label for exactly one output', () => {
    renderHeader('connected', { outputCount: 1, canvasOpen: false });
    expect(screen.getByTestId('chat-header-outputs-pill')).toHaveTextContent('1 output');
  });

  it('clicking the pill invokes onOpenCanvas (opens Canvas in-band)', () => {
    const onOpenCanvas = vi.fn();
    renderHeader('connected', { outputCount: 2, canvasOpen: false, onOpenCanvas });
    screen.getByTestId('chat-header-outputs-pill').click();
    expect(onOpenCanvas).toHaveBeenCalledTimes(1);
  });
});

describe('ChatHeader — pill respects lastSeenOutputCount (run_9dd59523, no nagging)', () => {
  it('HIDES the pill when all outputs have been seen (outputCount == lastSeen)', () => {
    renderHeader('connected', { outputCount: 3, lastSeenOutputCount: 3, canvasOpen: false });
    expect(screen.queryByTestId('chat-header-outputs-pill')).toBeNull();
  });

  it('SHOWS the pill only for NEW outputs beyond what was seen (outputCount > lastSeen)', () => {
    renderHeader('connected', { outputCount: 5, lastSeenOutputCount: 3, canvasOpen: false });
    expect(screen.getByTestId('chat-header-outputs-pill')).toBeInTheDocument();
  });

  it('defaults lastSeen to 0 (an older caller not passing it) → pill still shows for any output', () => {
    renderHeader('connected', { outputCount: 2, canvasOpen: false });
    expect(screen.getByTestId('chat-header-outputs-pill')).toBeInTheDocument();
  });
});
