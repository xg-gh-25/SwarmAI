import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { SessionTab } from './SessionTab';
import type { OpenTab } from '../types';

/**
 * Render tests for SessionTab active-state underline indicator.
 *
 * **Validates: blue active-tab underline (run_1866ea59)**
 * - AC1: Active tab renders border-b-2 + border-blue-500 (blue underline visible)
 * - AC2: Inactive tab renders border-b-2 + border-transparent (layout-shift-free, GUI12)
 *
 * GUI12: layout-shift-free border indicators put border-b-2 on ALL states,
 * transparent on the off state — so the underline appearing on activation
 * does not change the tab's height.
 */
describe('SessionTab active-state underline', () => {
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

  it('AC1: active tab has an accent bottom border (border-b-2 + border-[var(--color-primary)])', () => {
    // run_843962a5: active underline now uses the product accent var
    // (--color-primary, theme-aware) instead of a hardcoded blue-500.
    renderTab(true);
    const tab = screen.getByRole('tab');
    expect(tab.className).toContain('border-b-2');
    expect(tab.className).toContain('border-[var(--color-primary)]');
  });

  it('AC2: inactive tab has a transparent bottom border (border-b-2 + border-transparent)', () => {
    renderTab(false);
    const tab = screen.getByRole('tab');
    expect(tab.className).toContain('border-b-2');
    expect(tab.className).toContain('border-transparent');
  });

  it('AC2 (layout-shift-free): inactive tab does NOT carry the accent border color', () => {
    renderTab(false);
    const tab = screen.getByRole('tab');
    expect(tab.className).not.toContain('border-[var(--color-primary)]');
    expect(tab.className).not.toContain('border-blue-500');
  });

  it('both states reserve the border space — border-b-2 present regardless of active state', () => {
    const { unmount } = renderTab(true);
    expect(screen.getByRole('tab').className).toContain('border-b-2');
    unmount();
    renderTab(false);
    expect(screen.getByRole('tab').className).toContain('border-b-2');
  });
});
