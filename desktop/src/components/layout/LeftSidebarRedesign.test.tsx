/**
 * Tests for the LeftSidebar A10 redesign (run_1aab916c): horizontal row-cards.
 *
 * Behavioral contract of the A10 nav (things a snapshot can't assert cheaply):
 *   1. CHAT HERO — a hero card at the top carrying the SwarmAI brand logo.
 *   2. HISTORY ROW — a History entry directly under the Chat hero.
 *   3. THREE GROUPS in order — Cognitive (Context/Memory/Brain Hub),
 *      Work (ToDo/Workspace/Pipeline/Pollinate — daily-common pair first, A4),
 *      System (Capabilities/OS Eval/Settings/Community), each with a
 *      titled+colored group label.
 *   4. DOMAIN CARDS top-to-bottom in that exact order.
 *   5. Y/R SIGNAL FLAGS — none rendered (hardcoded literals removed 2026-08-03;
 *      the prop is retained for future real-signal-driven use only).
 *
 * Drives the REAL LeftSidebar through real providers (no mock of the component
 * under change — GUI32 prompt-source = answer-source). Only the pty boundary is
 * mocked (a render spawns nothing).
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, within, fireEvent } from '@testing-library/react';
import { LayoutProvider } from '../../contexts/LayoutContext';
import { OverlayProvider } from '../../contexts/OverlayContext';
import { TerminalProvider } from '../../contexts/TerminalContext';
import { ToastProvider } from '../../contexts/ToastContext';
import { LeftSidebar } from './ThreeColumnLayout';

vi.mock('../../services/pty', () => ({
  spawn: vi.fn(() => ({
    pid: 1,
    onData: vi.fn(() => ({ dispose: vi.fn() })),
    onExit: vi.fn(() => ({ dispose: vi.fn() })),
    write: vi.fn(),
    resize: vi.fn(),
    kill: vi.fn(),
  })),
}));

function renderSidebar() {
  return render(
    <ToastProvider>
      <LayoutProvider>
        <OverlayProvider>
          <TerminalProvider>
            <LeftSidebar />
          </TerminalProvider>
        </OverlayProvider>
      </LayoutProvider>
    </ToastProvider>,
  );
}

afterEach(() => cleanup());

// Render order (run_b57266d2): Cognition zone first (Context/Memory/Brain Hub +
// New Brain), then Work, then System. Cognition is a green PANEL (no group label),
// so it is NOT a navgroup-label — only Work + System carry labels now.
const DOMAIN_ORDER = [
  'nav-context',
  'nav-library',   // cognition zone: the bookshelf, above Brain Hub (XG 2026-08-02)
  'nav-brain-hub',
  'nav-new-brain',
  'nav-todo',     // A4: daily-common pair (ToDo + Workspace) first, `highlight`ed
  'nav-swarmws',  // labelled "Workspace" (de-jargoned; testid keeps nav-swarmws)
  // NOTE: no 'nav-canvas' — Canvas is output-triggered (auto-surface / chat file-chip
  // / agent command), not a nav card (run_990b0a03).
  'nav-pipeline',
  'nav-pollinate',
  'nav-jobs',      // System: Jobs & Runs FIRST in System (XG 2026-08-02)
  'nav-capabilities',
  'nav-eval',
  'nav-settings',
  'nav-community',
];

describe('LeftSidebar A10 — chat hero + history', () => {
  it('renders the Chat hero card carrying the SwarmAI brand logo', () => {
    renderSidebar();
    const hero = screen.getByTestId('chat-hero');
    expect(hero).toBeInTheDocument();
    // brand logo is an <svg> inside the hero (S-monogram)
    expect(hero.querySelector('svg')).not.toBeNull();
  });

  it('renders a History row under the Chat hero', () => {
    renderSidebar();
    expect(screen.getByTestId('history-row')).toBeInTheDocument();
  });

  // run_2bdc68ad — the 🔔 Alerts "Needs You" pill lives in a fixed left-chrome
  // slot (ChatPage portals the pill into this node), not on the tab row.
  it('renders the Alerts "Needs You" portal slot in the sidebar header, above the nav zone', () => {
    renderSidebar();
    const slot = screen.getByTestId('sidebar-alerts-slot');
    expect(slot).toBeInTheDocument();
    // It sits inside the fixed-width left-sidebar chrome (not the tab row).
    expect(screen.getByTestId('left-sidebar').contains(slot)).toBe(true);
    // …and before the domain nav zone (order: hero → history → alerts → nav).
    const nav = screen.getByTestId('nav-icons');
    expect(slot.compareDocumentPosition(nav) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});

describe('LeftSidebar A10 — cognition zone + groups + domain order', () => {
  it('renders the cognition zone as a green panel (not a labelled group)', () => {
    renderSidebar();
    // Cognition is a distinct panel container, NOT a titled navgroup.
    expect(screen.getByTestId('cognition-zone')).toBeInTheDocument();
    // Context (C&M)/Brain Hub live inside it. (Memory folded into the C&M overlay as a tab — no standalone nav card.)
    const zone = screen.getByTestId('cognition-zone');
    ['nav-context', 'nav-brain-hub', 'nav-library', 'nav-new-brain'].forEach((id) =>
      expect(within(zone).getByTestId(id)).toBeInTheDocument(),
    );
  });

  it('renders only Work + System as labelled groups (Cognitive is now a panel)', () => {
    renderSidebar();
    const nav = screen.getByTestId('nav-icons');
    const labels = Array.from(nav.querySelectorAll('[data-testid="navgroup-label"]')).map(
      (el) => el.textContent?.trim(),
    );
    expect(labels).toEqual(['Work', 'System']);
  });

  it('renders the domain entries top-to-bottom in zone/group order', () => {
    renderSidebar();
    const nav = screen.getByTestId('nav-icons');
    const ids = Array.from(nav.querySelectorAll('[data-testid^="nav-"]'))
      .map((b) => b.getAttribute('data-testid'))
      .filter((id) => id !== 'nav-icons');
    expect(ids).toEqual(DOMAIN_ORDER);
  });

  it('every domain card renders an inline-SVG icon (no CDN icon font)', () => {
    renderSidebar();
    for (const id of DOMAIN_ORDER) {
      const card = screen.getByTestId(id);
      expect(card.querySelector('svg')).not.toBeNull();
      // must NOT rely on the material-symbols icon font
      expect(card.querySelector('.material-symbols-outlined')).toBeNull();
    }
  });
});

describe('LeftSidebar A10 — Y/R signal flags', () => {
  // Contract (2026-08-03): flags are NOT shown as hardcoded literals — they were
  // permanent fake alarms (alarm fatigue). NO card renders a Y/R flag until one is
  // driven by a REAL signal (a live count/query). Until then, every card is flagless.
  // The A10Card `flag` prop + render branch are retained for that future real-signal
  // use; these tests pin "no static flags anywhere".
  it('shows NO Y flag on any card (no hardcoded alarm)', () => {
    renderSidebar();
    expect(screen.queryByTestId('flag-y')).toBeNull();
  });

  it('shows NO R flag on any card (no hardcoded alarm)', () => {
    renderSidebar();
    expect(screen.queryByTestId('flag-r')).toBeNull();
  });

  it('Brain Hub and OS Eval specifically carry no flag', () => {
    renderSidebar();
    expect(within(screen.getByTestId('nav-brain-hub')).queryByTestId('flag-y')).toBeNull();
    expect(within(screen.getByTestId('nav-eval')).queryByTestId('flag-r')).toBeNull();
  });
});

describe('LeftSidebar A10 — highlight tiers (run_edb48c31)', () => {
  it('highlights the Jobs & Runs card (high-frequency System entry)', () => {
    renderSidebar();
    // highlight prop → a10-card--hilite resting class
    expect(screen.getByTestId('nav-jobs').className).toContain('a10-card--hilite');
  });

  it('keeps the other System cards NON-highlighted (dim stays the default there)', () => {
    renderSidebar();
    for (const id of ['nav-capabilities', 'nav-settings', 'nav-community']) {
      expect(screen.getByTestId(id).className).not.toContain('a10-card--hilite');
    }
  });

  it('cognition zone carries the a10-zone container (accent-divider anchor)', () => {
    renderSidebar();
    // The right-edge accent spine is a ::after (untestable in jsdom cascade); the
    // class that carries it IS assertable — the zone container must render.
    expect(screen.getByTestId('cognition-zone').className).toContain('a10-zone');
  });
});

// The "nav-source spit-out origin" describe was removed 2026-08-04 (M5): A10Card no
// longer pushes a navSource singleton on click — the OverlayHost re-derives the source
// card's live rect from its data-testid (sourceCardTestId) at open time. The spout
// origin is now covered by OverlayHost.test (geometry contract).
