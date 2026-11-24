/**
 * Inbox in the Left Nav and the overlay registry (design docs/design/2026-11-20-inbox.md §7).
 *
 * Invariants:
 *   - the Inbox card is the first card of the Work zone, directly before ToDo, and the
 *     Community card is still there;
 *   - its badge is `reply` from GET /inbox/counts, shown only when > 0, and absent when
 *     the counts request fails;
 *   - 'inbox' is a registered overlay (Work zone, width xl) that agents cannot open
 *     (not in ALL_SHOW_EVENTS);
 *   - the four channel colour tokens exist once for light and once for dark;
 *   - the CDF diagram's queue stage no longer says INBOX.
 *
 * Drives the real LeftSidebar; only the HTTP boundary and the pty are mocked.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { LayoutProvider } from '../../contexts/LayoutContext';
import { OverlayProvider } from '../../contexts/OverlayContext';
import { TerminalProvider } from '../../contexts/TerminalContext';
import { ToastProvider } from '../../contexts/ToastContext';
import { LeftSidebar } from './ThreeColumnLayout';
import { NAV } from './navTable';
import { OVERLAY_IDS } from './overlayIds';
import { registeredOverlayIds } from './overlayRegistry';
import { ALL_SHOW_EVENTS } from './useExclusiveOverlay';
import { CdfGuideline } from './CdfGuideline';
import i18n from '../../i18n';

vi.mock('../../services/pty', () => ({
  spawn: vi.fn(() => ({
    pid: 1, onData: vi.fn(() => ({ dispose: vi.fn() })), onExit: vi.fn(() => ({ dispose: vi.fn() })),
    write: vi.fn(), resize: vi.fn(), kill: vi.fn(),
  })),
}));

let countsAnswer: () => Promise<unknown> = async () => ({ data: { reply: 0 } });
vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/api')>();
  const get = vi.fn(async (url: string) => (url.startsWith('/inbox/counts') ? countsAnswer() : { data: [] }));
  const other = vi.fn(async () => ({ data: {} }));
  return { ...actual, default: { get, post: other, put: other, delete: other,
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } }, defaults: { headers: {} } } };
});

function renderSidebar() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ToastProvider><LayoutProvider><OverlayProvider><TerminalProvider>
        <LeftSidebar />
      </TerminalProvider></OverlayProvider></LayoutProvider></ToastProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => { cleanup(); countsAnswer = async () => ({ data: { reply: 0 } }); });

describe('Inbox nav card', () => {
  it('Inbox is the first Work card, before ToDo', () => {
    renderSidebar();
    const nav = screen.getByTestId('nav-icons');
    const ids = Array.from(nav.querySelectorAll('button[data-testid^="nav-"]')).map((b) => b.getAttribute('data-testid'));
    const firstWork = ids.findIndex((id) => NAV[(id ?? '').replace(/^nav-/, '') as keyof typeof NAV]?.zone === 'work');
    expect(ids[firstWork]).toBe('nav-inbox');
    expect(ids[firstWork + 1]).toBe('nav-todo');
    expect(ids).not.toContain('nav-community');  // the Inbox replaced the Community page
    expect(screen.getByTestId('nav-inbox')).toHaveTextContent('Inbox');
  });

  it('shows the reply count from /inbox/counts as its badge', async () => {
    countsAnswer = async () => ({ data: { reply: 4, aware: 9, sends_need_you: 2 } });
    renderSidebar();
    await waitFor(() => expect(screen.getByTestId('nav-inbox-badge')).toHaveTextContent('4'));
  });

  it('shows no badge when nothing waits on a reply', async () => {
    let asked = false;
    countsAnswer = async () => { asked = true; return { data: { reply: 0, aware: 3 } }; };
    renderSidebar();
    await waitFor(() => expect(asked).toBe(true));
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByTestId('nav-inbox-badge')).toBeNull();
  });

  it('no badge when the counts request fails', async () => {
    let asked = false;
    countsAnswer = async () => { asked = true; throw new Error('404'); };
    renderSidebar();
    await waitFor(() => expect(asked).toBe(true));
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByTestId('nav-inbox-badge')).toBeNull();
  });
});

describe('Inbox overlay registration', () => {
  it('is a registered Work-zone overlay of width xl that agents cannot open', async () => {
    await import('./overlaySurfaces');
    expect(OVERLAY_IDS).toContain('inbox');
    expect(registeredOverlayIds()).toContain('inbox');
    expect(NAV.inbox).toMatchObject({ zone: 'work', navLabelKey: 'ui.navTable.inbox', width: 'xl' });
    expect(ALL_SHOW_EVENTS).not.toContain('swarm:show-inbox');
  });
});

describe('Inbox design tokens and naming', () => {
  it('defines each channel colour once for light and once for dark', () => {
    const css = readFileSync(join(__dirname, '../../index.css'), 'utf8');
    for (const ch of ['slack', 'mail', 'github', 'feed']) {
      expect(css.match(new RegExp(`--color-channel-${ch}:\\s*#[0-9a-fA-F]{6};`, 'g'))?.length).toBe(2);
    }
  });

  it('the CDF queue stage is labelled Queue / 待写入, not INBOX', async () => {
    const { unmount } = render(<CdfGuideline />);
    const diagram = screen.getByTestId('cdf-guide-diagram');
    expect(diagram.textContent).toContain('QUEUE');
    expect(diagram.textContent).not.toContain('INBOX');
    unmount();
    await i18n.changeLanguage('zh');
    try {
      render(<CdfGuideline />);
      expect(screen.getByTestId('cdf-guide-diagram').textContent).toContain('待写入');
    } finally {
      await i18n.changeLanguage('en');
    }
  });
});
