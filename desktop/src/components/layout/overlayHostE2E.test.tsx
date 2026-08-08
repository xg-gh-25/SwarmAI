/**
 * OverlayHost E2E — the show-event → open ACT contract, driven through the REAL
 * registry (run_fdeaead8, M4/M5). This is the regression guard for the exact bug the
 * M4 migration nearly shipped: after surfaces moved off the legacy useExclusiveOverlay
 * bus, a `swarm:show-<id>` event (the agent's ui_action ACT vocabulary + a nav card)
 * must OPEN the mapped surface — not close-only, not no-op.
 *
 * Unlike OverlayHost.test (which registers a throwaway surface to test geometry), this
 * mounts the ACTUAL `overlaySurfaces` registrations + OverlayProvider + OverlayHost and
 * fires the real window events, so a future migration that re-breaks the event→open
 * wiring (or renames an id off its event suffix) goes RED here.
 *
 * The surfaces self-fetch data; we mock the services to the empty/loading shape (this
 * test asserts the surface MOUNTS on its event, not its data rendering — that is each
 * surface's own suite). ChatPage-owned ctx bridge handlers are absent (undefined) —
 * exactly the pre-bridge state a render fn must null-guard.
 */
import { describe, it, expect, afterEach, vi, beforeEach } from 'vitest';
import { render, screen, act, cleanup } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { OverlayProvider } from '../../contexts/OverlayContext';
import { ExplorerProvider } from '../../contexts/ExplorerContext';
import { OverlayHost } from './OverlayHost';
import './overlaySurfaces'; // side-effect: register the REAL surfaces

// jsdom lacks ResizeObserver (used by explorer/message children of some surfaces).
class ResizeObserverStub { observe() {} unobserve() {} disconnect() {} }
(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k }) }));

// Stub every service a registered surface self-fetches, to a benign empty shape.
vi.mock('../../services/todos', () => ({
  todosService: {
    list: vi.fn().mockResolvedValue([]),
    history: vi.fn().mockResolvedValue({ todos: [], count: 0 }),
    historyStats: vi.fn().mockResolvedValue({
      throughputWeekly: [], completionRate: 0, sourceDistribution: {},
      confirmVsAuto: { manual: 0, auto: 0 }, rejectRate: 0,
      totals: { created: 0, completed: 0, confirmed: 0, rejected: 0, reviewed: 0 },
    }),
  },
}));
vi.mock('../../services/jobs', () => ({
  jobsService: {
    fetchRoster: vi.fn().mockResolvedValue([]),
    fetchOverview: vi.fn().mockResolvedValue(null),
  },
}));
vi.mock('../../services/pipelines', () => ({
  pipelinesService: {
    fetchAnalytics: vi.fn().mockResolvedValue({ overall: null, trend: [], byProject: [] }),
    fetchActivePipelines: vi.fn().mockResolvedValue([]),
  },
}));
vi.mock('../../services/pollinate', () => ({
  pollinateService: { fetchAssets: vi.fn().mockResolvedValue({ cards: [], overall: null }) },
  assetThumbUrl: (p: string) => p,
}));
// C&M / Library / Eval hit api.get; make it reject-safe (surface shows an error/empty,
// still MOUNTS — which is all this contract test asserts).
vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/api')>();
  return { ...actual, default: { get: vi.fn().mockResolvedValue({ data: {} }) } };
});
vi.mock('../../services/chat', () => ({
  chatService: {
    listSessions: vi.fn().mockResolvedValue([]),
    getSessionMessagesPaginated: vi.fn().mockResolvedValue([]),
  },
}));
vi.mock('../../services/agents', () => ({
  agentsService: {
    list: vi.fn().mockResolvedValue([]),
    getDefault: vi.fn().mockResolvedValue({ id: 'a1', name: 'Swarm' }),
  },
}));
// SwarmWS surface renders WorkspaceExplorer, which needs ExplorerProvider (in prod the
// host is nested inside it — overlaySurfaces.tsx documents this). Mock the tree service.
vi.mock('../../services/workspace', () => ({
  workspaceService: {
    getTree: vi.fn().mockResolvedValue([]),      // ExplorerContext expects a node ARRAY
    refreshTree: vi.fn().mockResolvedValue([]),
  },
}));

function renderHost() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ExplorerProvider>
        <OverlayProvider><OverlayHost /></OverlayProvider>
      </ExplorerProvider>
    </QueryClientProvider>,
  );
}

beforeEach(async () => {
  vi.clearAllMocks();
  // G2 (run_06c49540): brain-hub/settings/eval are React.lazy — pre-resolve their
  // dynamic import()s so this test exercises the event→open WIRING (its job), not the
  // chunk-fetch timing. Without this the Suspense fallback ("Loading…") is what
  // findByTestId sees for the lazy surfaces. The eager surfaces are unaffected.
  await Promise.all([
    import('./BrainHub'),
    import('../../pages/SettingsPage'),
    import('../../pages/EvalDashboard'),
  ]);
});
afterEach(cleanup);

// Each agent-openable surface: its swarm:show-<id> event → the content testid the
// registered render fn produces. This is the ACT contract, id-by-id.
const SURFACES: Array<{ event: string; testid: string; label: string }> = [
  { event: 'swarm:show-todo', testid: 'todo-overlay', label: 'ToDo' },
  { event: 'swarm:show-jobs', testid: 'jobs-overlay', label: 'Jobs & Runs' },
  { event: 'swarm:show-pipeline', testid: 'pipeline-overlay', label: 'Pipeline' },
  { event: 'swarm:show-pollinate', testid: 'pollinate-overlay', label: 'Pollinate' },
  { event: 'swarm:show-swarmws', testid: 'swarmws-overlay', label: 'SwarmWS' },
  { event: 'swarm:show-brain-hub', testid: 'brain-hub-overlay', label: 'Brain Hub' },
  { event: 'swarm:show-new-brain', testid: 'new-brain-overlay', label: 'New Brain' },
  // Community (run_5165013e) — fetches via api.get (mocked reject-safe above), so
  // it mounts to its empty state; the ACT contract is "the event opens the surface".
  { event: 'swarm:show-community', testid: 'community-overlay', label: 'Community' },
];

describe('OverlayHost E2E — swarm:show-<id> OPENS the mapped real surface (agent ACT contract)', () => {
  for (const s of SURFACES) {
    it(`${s.event} → opens ${s.label}`, async () => {
      renderHost();
      // nothing open initially
      expect(screen.queryByTestId('overlay-host-scrim')).toBeNull();
      // fire the exact event the agent ui_action / nav card dispatches
      await act(async () => {
        window.dispatchEvent(new CustomEvent(s.event));
        await Promise.resolve();
      });
      // the host mounted the mapped surface (scrim up + this surface's content)
      expect(screen.getByTestId('overlay-host-scrim')).toBeInTheDocument();
      expect(await screen.findByTestId(s.testid)).toBeInTheDocument();
    });
  }

  it('a second show-event REPLACES the first (single-slot mutual exclusion)', async () => {
    renderHost();
    await act(async () => { window.dispatchEvent(new CustomEvent('swarm:show-todo')); await Promise.resolve(); });
    expect(await screen.findByTestId('todo-overlay')).toBeInTheDocument();
    await act(async () => { window.dispatchEvent(new CustomEvent('swarm:show-jobs')); await Promise.resolve(); });
    // ToDo gone, Jobs shown — exactly one surface open
    expect(screen.queryByTestId('todo-overlay')).toBeNull();
    expect(await screen.findByTestId('jobs-overlay')).toBeInTheDocument();
  });

  it('back-to-chat closes the open surface', async () => {
    renderHost();
    await act(async () => { window.dispatchEvent(new CustomEvent('swarm:show-context')); await Promise.resolve(); });
    expect(screen.getByTestId('overlay-host-scrim')).toBeInTheDocument();
    await act(async () => {
      window.dispatchEvent(new CustomEvent('swarm:back-to-chat'));
      await Promise.resolve();
      // advance past the exit-transition unmount backstop
      await new Promise((r) => setTimeout(r, 350));
    });
    expect(screen.queryByTestId('overlay-host-scrim')).toBeNull();
  });
});
