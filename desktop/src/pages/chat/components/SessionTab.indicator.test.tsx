import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { SessionTab } from './SessionTab';
import type { OpenTab } from '../types';

/**
 * Render tests for SessionTab active-state styling (2026-10-22 redesign).
 *
 * - Active: a raised card — card surface, the border color, a 2px accent rule on top
 *   (inset shadow in --color-primary), full-strength text.
 * - Inactive: flat — transparent border, muted text, no accent rule.
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

  it('active tab is a raised card with the accent rule on top', () => {
    renderTab(true);
    const c = screen.getByRole('tab').className;
    expect(c).toContain('bg-[var(--color-card)]');
    expect(c).toContain('border-[var(--color-border)]');
    expect(c).toContain('inset_0_2px_0_var(--color-primary)');
    expect(c).toContain('text-[var(--color-text)]');
  });

  it('inactive tab is flat and muted, without the accent rule', () => {
    renderTab(false);
    const c = screen.getByRole('tab').className;
    expect(c).toContain('border-transparent');
    expect(c).toContain('text-[var(--color-text-muted)]');
    expect(c).not.toContain('var(--color-primary)');
    expect(c).not.toContain('bg-[var(--color-card)]');
  });

  it('both states have the same border width and height (no shift on select)', () => {
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
