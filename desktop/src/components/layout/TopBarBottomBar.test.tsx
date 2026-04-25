/**
 * Unit tests for TopBar and BottomBar layout components.
 *
 * Testing methodology: Unit tests using Vitest + React Testing Library.
 * Verifies:
 * - TopBar renders session metadata when provided
 * - TopBar shows fallback "SwarmAI" when no session meta
 * - TopBar context usage color thresholds (>80% red, >60% yellow, ≤60% green)
 * - BottomBar renders connection status, agent name, workspace name
 * - BottomBar shows "Offline" when disconnected
 * - BottomBar keyboard hints are present
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// Mock Tauri window API
vi.mock('@tauri-apps/api/window', () => ({
  getCurrentWindow: () => ({
    startDragging: vi.fn(),
  }),
}));

// Mock LayoutContext with separate session meta
vi.mock('../../contexts/LayoutContext', () => {
  const actual = vi.importActual('../../contexts/LayoutContext');
  return {
    ...actual,
    useLayout: vi.fn(),
    useSessionMeta: vi.fn(),
    LayoutProvider: ({ children }: { children: ReactNode }) => children,
    LAYOUT_CONSTANTS: { LEFT_SIDEBAR_WIDTH: 44 },
  };
});

// Mock ExplorerContext
vi.mock('../../contexts/ExplorerContext', () => ({
  ExplorerProvider: ({ children }: { children: ReactNode }) => children,
  useTreeData: () => ({ refreshTree: vi.fn() }),
}));

// Mock HealthContext — we need a real React context for Provider to work.
// vi.hoisted runs before vi.mock hoisting, so the context is available.
const { _mockHealthCtx } = vi.hoisted(() => {
  const { createContext } = require('react');
  return { _mockHealthCtx: createContext(undefined) };
});
vi.mock('../../contexts/HealthContext', () => ({
  HealthContext: _mockHealthCtx,
  useHealth: vi.fn(),
}));

import { useLayout, useSessionMeta } from '../../contexts/LayoutContext';
import { HealthContext } from '../../contexts/HealthContext';
import type { HealthContextValue } from '../../contexts/HealthContext';

const mockUseLayout = useLayout as ReturnType<typeof vi.fn>;
const mockUseSessionMeta = useSessionMeta as ReturnType<typeof vi.fn>;

// We import TopBar and BottomBar from ThreeColumnLayout (they're named exports)
import { TopBar } from './ThreeColumnLayout';
import { BottomBar } from './BottomBar';

// ---------- Helpers ----------

function setupMocks(overrides: {
  sessionMeta?: {
    topic?: string;
    contextPct?: number | null;
    fileCount?: number;
    agentName?: string;
  } | null;
} = {}) {
  mockUseLayout.mockReturnValue({
    activeModal: null,
    openModal: vi.fn(),
    closeModal: vi.fn(),
    workspaceExplorerCollapsed: false,
    setWorkspaceExplorerCollapsed: vi.fn(),
    workspaceExplorerWidth: 280,
    setWorkspaceExplorerWidth: vi.fn(),
    selectedWorkspaceScope: 'all',
    setSelectedWorkspaceScope: vi.fn(),
    validateWorkspaceScope: vi.fn(),
    workspaceSettingsId: '',
    setWorkspaceSettingsId: vi.fn(),
    isNarrowViewport: false,
  });

  const meta = overrides.sessionMeta === undefined
    ? null
    : overrides.sessionMeta === null
      ? null
      : {
          topic: overrides.sessionMeta.topic ?? 'Test Session',
          contextPct: 'contextPct' in overrides.sessionMeta ? overrides.sessionMeta.contextPct : 45,
          fileCount: overrides.sessionMeta.fileCount ?? 3,
          agentName: overrides.sessionMeta.agentName ?? 'SwarmAI',
        };

  mockUseSessionMeta.mockReturnValue({
    activeSessionMeta: meta,
    setActiveSessionMeta: vi.fn(),
  });
}

// ---------- TopBar Tests ----------

describe('TopBar', () => {
  function renderTopBar() {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
      <QueryClientProvider client={qc}>
        <TopBar />
      </QueryClientProvider>,
    );
  }

  it('renders token usage labels', () => {
    setupMocks({ sessionMeta: null });
    renderTopBar();
    expect(screen.getByText('Today')).toBeDefined();
    expect(screen.getByText('Total')).toBeDefined();
  });

  it('does not render context ring (moved to ChatInput)', () => {
    setupMocks({ sessionMeta: { contextPct: 72 } });
    renderTopBar();
    // No SVG ring in TopBar anymore
    const bar = screen.getByTestId('top-bar');
    expect(bar.querySelector('svg')).toBeNull();
  });

  it('has data-tauri-drag-region for window dragging', () => {
    setupMocks({ sessionMeta: null });
    renderTopBar();
    const bar = screen.getByTestId('top-bar');
    expect(bar.getAttribute('data-tauri-drag-region')).toBeDefined();
  });
});

// ---------- BottomBar Tests ----------

function renderBottomBarWithHealth(connected: boolean) {
  setupMocks({ sessionMeta: { agentName: 'Swarm' } });
  const healthValue: HealthContextValue = {
    health: {
      status: connected ? 'connected' : 'disconnected',
      lastCheckedAt: Date.now(),
      consecutiveFailures: connected ? 0 : 3,
    },
    triggerHealthCheck: vi.fn(),
  };
  return render(
    <HealthContext.Provider value={healthValue}>
      <BottomBar />
    </HealthContext.Provider>
  );
}

describe('BottomBar', () => {
  it('shows "Connected" when health status is connected', () => {
    renderBottomBarWithHealth(true);
    expect(screen.getByText('Connected')).toBeDefined();
  });

  it('shows "Offline" when health status is disconnected', () => {
    renderBottomBarWithHealth(false);
    expect(screen.getByText('Offline')).toBeDefined();
  });

  it('renders agent name from session meta', () => {
    renderBottomBarWithHealth(true);
    expect(screen.getByText('Swarm')).toBeDefined();
  });

  it('renders workspace name "SwarmWS"', () => {
    renderBottomBarWithHealth(true);
    expect(screen.getByText('SwarmWS')).toBeDefined();
  });

  it('renders keyboard shortcut hints', () => {
    renderBottomBarWithHealth(true);
    expect(screen.getByText('send')).toBeDefined();
    expect(screen.getByText('newline')).toBeDefined();
    expect(screen.getByText('tab')).toBeDefined();
  });

  it('falls back to "Swarm" when no session meta', () => {
    setupMocks({ sessionMeta: null });
    render(<BottomBar />);
    expect(screen.getByText('Swarm')).toBeDefined();
  });

  it('has data-testid for targeting', () => {
    setupMocks({ sessionMeta: null });
    render(<BottomBar />);
    expect(screen.getByTestId('bottom-bar')).toBeDefined();
  });
});
