/**
 * Unit tests for TopBar and BottomBar layout components.
 *
 * Testing methodology: Unit tests using Vitest + React Testing Library.
 * Verifies:
 * - TopBar renders session metadata when provided
 * - TopBar shows fallback "SwarmAI" when no session meta
 * - TopBar context usage color thresholds (>80% red, >60% amber)
 * - BottomBar renders connection status, agent name, workspace name
 * - BottomBar shows "Offline" when disconnected
 * - BottomBar keyboard hints are present
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ReactNode } from 'react';

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
  it('shows fallback "SwarmAI" when no session meta is set', () => {
    setupMocks({ sessionMeta: null });
    render(<TopBar />);
    expect(screen.getByText('SwarmAI')).toBeDefined();
  });

  it('renders session topic when meta is provided', () => {
    setupMocks({ sessionMeta: { topic: 'My Chat' } });
    render(<TopBar />);
    expect(screen.getByText('My Chat')).toBeDefined();
  });

  it('renders "New Session" when topic is empty string', () => {
    setupMocks({ sessionMeta: { topic: '' } });
    render(<TopBar />);
    expect(screen.getByText('New Session')).toBeDefined();
  });

  it('renders context percentage', () => {
    setupMocks({ sessionMeta: { contextPct: 72 } });
    render(<TopBar />);
    expect(screen.getByText('72%')).toBeDefined();
  });

  it('renders "--" when contextPct is null', () => {
    setupMocks({ sessionMeta: { contextPct: null } });
    render(<TopBar />);
    const pctLabel = screen.getByLabelText(/Context usage: unknown/);
    expect(pctLabel).toBeDefined();
    expect(pctLabel.textContent).toContain('--');
  });

  it('renders file count', () => {
    setupMocks({ sessionMeta: { fileCount: 5 } });
    render(<TopBar />);
    expect(screen.getByText('5')).toBeDefined();
  });

  it('renders agent name', () => {
    setupMocks({ sessionMeta: { agentName: 'TestAgent' } });
    render(<TopBar />);
    expect(screen.getByText('TestAgent')).toBeDefined();
  });

  it('applies red color class when context > 80%', () => {
    setupMocks({ sessionMeta: { contextPct: 85 } });
    render(<TopBar />);
    const pctLabel = screen.getByLabelText(/Context usage: 85%/);
    expect(pctLabel.className).toContain('text-red-400');
  });

  it('applies amber color class when context > 60%', () => {
    setupMocks({ sessionMeta: { contextPct: 65 } });
    render(<TopBar />);
    const pctLabel = screen.getByLabelText(/Context usage: 65%/);
    expect(pctLabel.className).toContain('text-amber-400');
  });

  it('applies muted color class when context <= 60%', () => {
    setupMocks({ sessionMeta: { contextPct: 40 } });
    render(<TopBar />);
    const pctLabel = screen.getByLabelText(/Context usage: 40%/);
    expect(pctLabel.className).toContain('text-[var(--color-text-muted)]');
  });

  it('has data-tauri-drag-region for window dragging', () => {
    setupMocks({ sessionMeta: null });
    render(<TopBar />);
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

  it('falls back to "SwarmAI" when no session meta', () => {
    setupMocks({ sessionMeta: null });
    render(<BottomBar />);
    expect(screen.getByText('SwarmAI')).toBeDefined();
  });

  it('has data-testid for targeting', () => {
    setupMocks({ sessionMeta: null });
    render(<BottomBar />);
    expect(screen.getByTestId('bottom-bar')).toBeDefined();
  });
});
