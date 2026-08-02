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

function renderHeader(healthStatus: HealthContextValue['health']['status'] = 'connected') {
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
