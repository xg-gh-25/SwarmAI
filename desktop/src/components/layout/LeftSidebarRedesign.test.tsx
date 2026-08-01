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
 *   5. Y/R SIGNAL FLAGS — Memory=Y, Brain Hub=Y, OS Eval=R, none elsewhere.
 *
 * Drives the REAL LeftSidebar through real providers (no mock of the component
 * under change — GUI32 prompt-source = answer-source). Only the pty boundary is
 * mocked (a render spawns nothing).
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, within, fireEvent } from '@testing-library/react';
import { LayoutProvider } from '../../contexts/LayoutContext';
import { TerminalProvider } from '../../contexts/TerminalContext';
import { ToastProvider } from '../../contexts/ToastContext';
import { LeftSidebar } from './ThreeColumnLayout';
import { readNavSource, clearNavSource } from './navSource';

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
        <TerminalProvider>
          <LeftSidebar />
        </TerminalProvider>
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
  'nav-brain-hub',
  'nav-new-brain',
  'nav-todo',     // A4: daily-common pair (ToDo + Workspace) first, `highlight`ed
  'nav-swarmws',  // labelled "Workspace" (de-jargoned; testid keeps nav-swarmws)
  'nav-pipeline',
  'nav-pollinate',
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
});

describe('LeftSidebar A10 — cognition zone + groups + domain order', () => {
  it('renders the cognition zone as a green panel (not a labelled group)', () => {
    renderSidebar();
    // Cognition is a distinct panel container, NOT a titled navgroup.
    expect(screen.getByTestId('cognition-zone')).toBeInTheDocument();
    // Context (C&M)/Brain Hub live inside it. (Memory folded into the C&M overlay as a tab — no standalone nav card.)
    const zone = screen.getByTestId('cognition-zone');
    ['nav-context', 'nav-brain-hub', 'nav-new-brain'].forEach((id) =>
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
  it('shows a Y flag on Brain Hub', () => {
    renderSidebar();
    expect(within(screen.getByTestId('nav-brain-hub')).getByTestId('flag-y')).toBeInTheDocument();
  });

  it('shows an R flag on OS Eval', () => {
    renderSidebar();
    expect(within(screen.getByTestId('nav-eval')).getByTestId('flag-r')).toBeInTheDocument();
  });

  it('shows NO flag on the other domains (no-news = no flag)', () => {
    renderSidebar();
    for (const id of ['nav-context', 'nav-pipeline', 'nav-pollinate', 'nav-swarmws', 'nav-capabilities', 'nav-settings', 'nav-community']) {
      const card = screen.getByTestId(id);
      expect(within(card).queryByTestId('flag-y')).toBeNull();
      expect(within(card).queryByTestId('flag-r')).toBeNull();
    }
  });
});

describe('LeftSidebar A10 — nav-source spit-out origin (spout wiring)', () => {
  afterEach(() => clearNavSource());

  it('a fullscreen-opening card (SwarmWS) publishes a nav source', () => {
    clearNavSource();
    renderSidebar();
    fireEvent.click(screen.getByTestId('nav-swarmws'));
    // A10Card captured the card rect (jsdom rects are 0 but the object is set)
    expect(readNavSource()).not.toBeNull();
  });
});
