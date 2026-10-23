/**
 * Left Nav × overlay contract (design docs/design/left-nav-overlay-single-source.md, P0).
 *
 * Renders the REAL LeftSidebar next to the REAL OverlayHost + registered surfaces,
 * through the real providers. Only the HTTP boundary (services/api) and the pty are
 * mocked: every overlay's own service code runs, so a fetch counted here is a real
 * request the app would make. For each nav item it checks the target behaviour the
 * design fixes (XG decisions 2026-10-23: D1 a second click closes, D2 Settings is
 * selected when opened at a sub-tab, D3 the chat zone uses the theme colour, D4 the
 * overlay title is the nav label):
 *
 *   tint        the panel accent (--panel-accent) is the item's zone colour
 *   title       the overlay header title equals the nav label
 *   spout       the spout points at the item that opened it
 *   selected    the item shows selected while its overlay is open
 *   toggle      a second click closes the overlay
 *   modalFirst  clicking an item closes an open small modal first
 *   refetch     a second open within the cache window sends no new request
 *   prefetch    hovering the item fetches the overlay's data before the click
 *
 * Every failure is a signature `<id>:<check>`. The set of signatures must EQUAL
 * `KNOWN` — a new divergence fails, and so does a fixed one still listed (each phase
 * removes what it fixed, so the list only shrinks). The design is done when it is empty.
 */
import { describe, it, expect, vi, afterEach, beforeAll } from 'vitest';
import { render, cleanup, act, fireEvent, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useEffect, type ReactNode } from 'react';
import { LayoutProvider, useLayout } from '../../contexts/LayoutContext';
import { OverlayProvider, useOverlay } from '../../contexts/OverlayContext';
import { TerminalProvider } from '../../contexts/TerminalContext';
import { ToastProvider } from '../../contexts/ToastContext';
import { ExplorerProvider } from '../../contexts/ExplorerContext';
import { ThemeProvider } from '../../contexts/ThemeContext';
import { HealthProvider } from '../../contexts/HealthContext';
import { LeftSidebar } from './ThreeColumnLayout';
import { OverlayHost } from './OverlayHost';
import './overlaySurfaces';
import { OVERLAY_CHUNK } from './overlayChunks';

class ResizeObserverStub { observe() {} unobserve() {} disconnect() {} }
(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k, i18n: { language: 'en' } }) }));
vi.mock('../../services/tauri', async () => {
  const actual = await vi.importActual<typeof import('../../services/tauri')>('../../services/tauri');
  return { ...actual, isDesktop: () => true };
});
vi.mock('../../services/pty', () => ({
  spawn: vi.fn(() => ({
    pid: 1, onData: vi.fn(() => ({ dispose: vi.fn() })), onExit: vi.fn(() => ({ dispose: vi.fn() })),
    write: vi.fn(), resize: vi.fn(), kill: vi.fn(),
  })),
}));

// The HTTP boundary: every GET is recorded and answered with an empty shape.
const calls: string[] = [];
const OBJECT_URLS = ['/ddd/brains', '/jobs/status', '/eval/health', '/eval/golden-set', '/eval/governance',
  '/eval/context-health', '/eval/brain', '/library/native', '/library/recent',
  '/library/mounts', '/attention', '/community/engagement', '/community/hot-topics', '/settings',
  '/pollinate/assets', '/pipelines/analytics', '/todos/history'];
function answer(url: string): unknown {
  if (url.startsWith('/ddd/brains')) return { brains: [], pinned: [] };
  return OBJECT_URLS.some((p) => url.startsWith(p)) ? {} : [];
}
vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/api')>();
  const get = vi.fn(async (url: string) => { calls.push(url); return { data: answer(url), headers: {}, status: 200 }; });
  const other = vi.fn(async () => ({ data: {}, headers: {}, status: 200 }));
  return { ...actual, default: { get, post: other, put: other, patch: other, delete: other,
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } }, defaults: { headers: {} } } };
});

// services/chat reaches services/api through an import cycle, so it holds the REAL
// axios instance, not the mock above: record its session list here instead.
vi.mock('../../services/chat', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/chat')>();
  return { ...actual, chatService: { ...actual.chatService,
    listSessions: vi.fn(async () => { calls.push('/chat/sessions'); return []; }) } };
});

// ── The target spec (one row per nav item) ─────────────────────────────────────
const ZONE = { chat: 'var(--color-primary)', cognitive: 'var(--color-zone-cognitive)', work: 'var(--color-zone-work)', system: 'var(--color-zone-system)' } as const;
interface Item { id: string; nav: string; label: string; zone: keyof typeof ZONE; fetches?: string[] }
const ITEMS: Item[] = [
  { id: 'history', nav: 'history-row', label: 'History', zone: 'chat', fetches: ['/chat/sessions'] },
  { id: 'artifacts', nav: 'artifacts-row', label: 'Artifacts', zone: 'chat', fetches: ['/artifacts'] },
  { id: 'context', nav: 'nav-context', label: 'C&M', zone: 'cognitive', fetches: ['/eval/context-health', '/eval/brain'] },
  { id: 'library', nav: 'nav-library', label: 'Library', zone: 'cognitive', fetches: ['/library'] },
  { id: 'brain-hub', nav: 'nav-brain-hub', label: 'Brain Hub', zone: 'cognitive', fetches: ['/ddd/brains'] },
  { id: 'new-brain', nav: 'nav-new-brain', label: 'New Brain', zone: 'cognitive' },
  { id: 'todo', nav: 'nav-todo', label: 'ToDo', zone: 'work', fetches: ['/todos'] },
  { id: 'swarmws', nav: 'nav-swarmws', label: 'Workspace', zone: 'work' },
  { id: 'pipeline', nav: 'nav-pipeline', label: 'Pipeline', zone: 'work', fetches: ['/pipelines'] },
  { id: 'pollinate', nav: 'nav-pollinate', label: 'Pollinate', zone: 'work', fetches: ['/pollinate'] },
  { id: 'community', nav: 'nav-community', label: 'Community', zone: 'work', fetches: ['/community'] },
  { id: 'jobs', nav: 'nav-jobs', label: 'Jobs & Runs', zone: 'system', fetches: ['/jobs'] },
  { id: 'capabilities', nav: 'nav-capabilities', label: 'Capabilities', zone: 'system', fetches: ['/skills', '/mcp'] },
  { id: 'hive', nav: 'nav-hive', label: 'Hive', zone: 'system', fetches: ['/hive/accounts'] },
  // Settings / OS Eval fetching is out of scope (design §7): no refetch/prefetch checks.
  { id: 'eval', nav: 'nav-eval', label: 'OS Eval', zone: 'system' },
  { id: 'settings', nav: 'nav-settings', label: 'Settings', zone: 'system' },
];

// Divergences on main at P0 (2026-10-23), grouped by the design's root cause.
// Each phase deletes what it fixed; the design is done when this is empty.
const KNOWN = new Set<string>([
]);

// ── Harness ────────────────────────────────────────────────────────────────────
let probe: { layout: ReturnType<typeof useLayout>; overlay: ReturnType<typeof useOverlay> } | null = null;
function Probe() {
  const layout = useLayout();
  const overlay = useOverlay();
  useEffect(() => { probe = { layout, overlay }; });
  return null;
}

function renderApp() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrap = (c: ReactNode) => (
    <ThemeProvider><QueryClientProvider client={qc}><ToastProvider><HealthProvider><LayoutProvider><ExplorerProvider>
      <OverlayProvider><TerminalProvider>{c}</TerminalProvider></OverlayProvider>
    </ExplorerProvider></LayoutProvider></HealthProvider></ToastProvider></QueryClientProvider></ThemeProvider>
  );
  return render(wrap(<><LeftSidebar /><div style={{ position: 'relative' }}><OverlayHost /></div><Probe /></>));
}

const settle = async (ms = 0) => {
  await act(async () => {
    for (let i = 0; i < 5; i++) await Promise.resolve();
    await new Promise((r) => setTimeout(r, ms));
    for (let i = 0; i < 5; i++) await Promise.resolve();
  });
};
const EXIT = 350; // > OverlayHost EXIT_MS
const isOpen = () => screen.queryByTestId('overlay-host-scrim') != null;
const count = (prefixes: string[]) => calls.filter((u) => prefixes.some((p) => u.startsWith(p))).length;
const panel = () => screen.getByTestId('overlay-host-scrim').firstElementChild as HTMLElement;

async function checkItem(it: Item, fail: (check: string) => void) {
  const el = () => screen.getByTestId(it.nav);

  // prefetch: hovering fetches before the click
  if (it.fetches) {
    const before = count(it.fetches);
    fireEvent.mouseEnter(el());
    fireEvent.focus(el());
    await settle();
    if (count(it.fetches) === before) fail('prefetch');
  }

  fireEvent.click(el());
  await settle(20);
  if (!isOpen()) { fail('opens'); return; }
  if (panel().style.getPropertyValue('--panel-accent') !== ZONE[it.zone]) fail('tint');
  if (panel().querySelector('h2')?.textContent !== it.label) fail('title');
  if (!screen.queryByTestId('overlay-host-spout')) fail('spout');
  if (el().getAttribute('aria-pressed') !== 'true') fail('selected');

  // toggle: a second click closes
  fireEvent.click(el());
  await settle(EXIT);
  if (isOpen()) {
    fail('toggle');
    act(() => { probe!.overlay.closeOverlay(); });
    await settle(EXIT);
  }

  // refetch: reopen inside the cache window → no new request
  if (it.fetches) {
    const before = count(it.fetches);
    fireEvent.click(el());
    await settle(20);
    if (count(it.fetches) > before) fail('refetch');
    act(() => { probe!.overlay.closeOverlay(); });
    await settle(EXIT);
  }

  // modalFirst: an open small modal closes when a nav item is clicked
  act(() => { probe!.layout.openModal('workspace-settings'); });
  await settle();
  fireEvent.click(el());
  await settle(20);
  if (probe!.layout.activeModal != null) fail('modalFirst');
  act(() => { probe!.layout.closeModal(); probe!.overlay.closeOverlay(); });
  await settle(EXIT);
}

beforeAll(async () => {
  await Promise.all(Object.values(OVERLAY_CHUNK).map((load) => load()));   // every content is lazy
});
afterEach(() => { cleanup(); probe = null; });

describe('Left Nav × overlay contract', () => {
  it('every nav item matches the spec; divergences equal KNOWN exactly', async () => {
    const found = new Set<string>();
    for (const it of ITEMS) {
      calls.length = 0;
      renderApp();
      await settle();
      // ChatPage publishes the current agent (History is per agent)
      act(() => { probe!.overlay.setAgentId('agent-1'); });
      await settle();
      await checkItem(it, (c) => found.add(`${it.id}:${c}`));
      cleanup();
    }
    // D2: Settings opened at a sub-tab still shows the Settings item selected
    renderApp();
    await settle();
    act(() => { probe!.layout.setSettingsTab('general' as never); probe!.overlay.openOverlay('settings'); });
    await settle(20);
    if (screen.getByTestId('nav-settings').getAttribute('aria-pressed') !== 'true') found.add('settings:selectedAtTab');
    cleanup();
    // Need You opens from the alerts pill, not a nav card: zone colour + title only
    renderApp();
    await settle();
    act(() => { probe!.overlay.openOverlay('needs-you'); });
    await settle(20);
    if (panel().style.getPropertyValue('--panel-accent') !== ZONE.chat) found.add('needs-you:tint');
    if (panel().querySelector('h2')?.textContent !== 'Need You') found.add('needs-you:title');

    const sorted = (s: Set<string>) => [...s].sort();
    expect(sorted(found)).toEqual(sorted(KNOWN));
  }, 60_000);
});
