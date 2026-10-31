import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { SessionTab } from './SessionTab';
import type { OpenTab } from '../types';

const CSS = readFileSync(join(__dirname, '..', '..', '..', 'index.css'), 'utf8');

/**
 * Render tests for SessionTab active-state styling (2026-10-24 piano keys).
 *
 * - Inactive: a key in the shared Nav key material (.session-tab), muted text.
 * - Active: .session-tab--active — the chat body colour, a 2px accent rule on top
 *   (inset shadow in --color-primary), full-strength text, and a foot that joins
 *   the chat below (checked against index.css).
 * - Both states carry a 1px border and the same 30px height, so selecting a tab
 *   never shifts the strip (GUI12).
 */
describe('SessionTab active-state styling', () => {
  const mockTab: OpenTab = {
    id: 'tab-0',
    title: 'Test Tab',
    agentId: 'agent-1',
    isNew: false,
  };

  const renderTab = (isActive: boolean) =>
    render(
      <SessionTab
        tab={mockTab}
        index={0}
        isActive={isActive}
        onSelect={vi.fn()}
        onClose={vi.fn()}
      />
    );

  it('active tab carries the joined-key class and full-strength text', () => {
    renderTab(true);
    const c = screen.getByRole('tab').className;
    expect(c).toContain('session-tab--active');
    expect(c).toContain('text-[var(--color-text)]');
  });

  it('inactive tab is a plain key, muted, without the active class', () => {
    renderTab(false);
    const c = screen.getByRole('tab').className;
    expect(c).toContain('session-tab');
    expect(c).not.toContain('session-tab--active');
    expect(c).toContain('text-[var(--color-text-muted)]');
  });

  it('the CSS gives the active tab the accent rule and the chat body colour', () => {
    const block = CSS.match(/\.session-tab--active\s*\{([^}]*)\}/)?.[1] ?? '';
    expect(block).toMatch(/background:\s*var\(--color-bg\)/);
    expect(block).toMatch(/inset 0 2px 0 var\(--color-primary\)/);
    // the foot reaches the strip's bottom edge: padding-box bottom + 1px border + 5px
    expect(CSS).toMatch(/\.session-tab--active::before\s*\{[^}]*top:\s*100%[^}]*height:\s*6px/);
    // inactive tabs use the shared key material
    expect(CSS).toMatch(/\.session-tab\s*\{[^}]*background:\s*var\(--nav-key-face\)/);
  });

  it('both states have the same border and height (no shift on select)', () => {
    const { unmount } = renderTab(true);
    const a = screen.getByRole('tab').className.split(/\s+/);
    unmount();
    renderTab(false);
    const b = screen.getByRole('tab').className.split(/\s+/);
    for (const cls of ['border', 'h-[30px]']) {
      expect(a).toContain(cls);
      expect(b).toContain(cls);
    }
  });
});

describe('SessionTab — background agents after the turn ended', () => {
  const tab: OpenTab = { id: 'tab-bg', title: 'Work', agentId: 'agent-1', isNew: false, sessionId: 'sess-bg' };
  const renderTab = (status?: 'streaming' | 'idle') =>
    render(<SessionTab tab={tab} index={0} isActive={false} onSelect={vi.fn()} onClose={vi.fn()} status={status} />);

  it('idle with running background agents: a working ring, their count and what each does', async () => {
    const { backgroundAgentsStore } = await import('../../../stores/backgroundAgentsStore');
    backgroundAgentsStore.apply('sess-bg', [
      { toolUseId: 'a', status: 'running', description: 'Review the parser' },
      { toolUseId: 'b', status: 'running', description: 'Search the docs' },
      { toolUseId: 'c', status: 'completed', description: 'Done one' },
    ]);
    try {
      renderTab('idle');
      const el = screen.getByTestId('tab-background-agents');
      expect(el.textContent).toBe('2');
      expect(el.getAttribute('title')).toContain('Review the parser');
      expect(el.getAttribute('title')).not.toContain('Done one');
    } finally {
      backgroundAgentsStore.apply('sess-bg', []);
    }
  });

  it('while the turn streams the streaming dot says it; none running shows nothing', async () => {
    const { backgroundAgentsStore } = await import('../../../stores/backgroundAgentsStore');
    backgroundAgentsStore.apply('sess-bg', [{ toolUseId: 'a', status: 'running', description: 'x' }]);
    try {
      const view = renderTab('streaming');
      expect(view.queryByTestId('tab-background-agents')).toBeNull();
      view.unmount();
      backgroundAgentsStore.apply('sess-bg', [{ toolUseId: 'a', status: 'completed', description: 'x' }]);
      renderTab('idle');
      expect(screen.queryByTestId('tab-background-agents')).toBeNull();
    } finally {
      backgroundAgentsStore.apply('sess-bg', []);
    }
  });
});

describe('SessionTab — background agents wording', () => {
  it('one agent reads in the singular', async () => {
    const i18n = (await import('../../../i18n')).default;
    const { backgroundAgentsStore } = await import('../../../stores/backgroundAgentsStore');
    await i18n.changeLanguage('en');
    backgroundAgentsStore.apply('sess-one', [{ toolUseId: 'a', status: 'running', description: 'x' }]);
    try {
      render(<SessionTab tab={{ id: 't-one', title: 'W', agentId: 'a', isNew: false, sessionId: 'sess-one' }}
        index={0} isActive={false} onSelect={vi.fn()} onClose={vi.fn()} status="idle" />);
      expect(screen.getByTestId('tab-background-agents').getAttribute('aria-label')).toBe('1 background agent running');
    } finally {
      backgroundAgentsStore.apply('sess-one', []);
    }
  });
});
