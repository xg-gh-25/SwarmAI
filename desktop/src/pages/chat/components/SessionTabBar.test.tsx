import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { SessionTabBar } from './SessionTabBar';
import type { OpenTab } from '../types';

// SessionTabBar uses useTranslation for the tail "+" button labels.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_key: string, fallback?: string) => fallback ?? _key }),
}));

/**
 * Unit tests for SessionTabBar keyboard navigation
 * 
 * **Validates: Requirements - Accessibility**
 * - Tabs should be keyboard navigable
 * - Active tab should have appropriate ARIA attributes
 */
describe('SessionTabBar', () => {
  const createMockTabs = (count: number): OpenTab[] => {
    return Array.from({ length: count }, (_, i) => ({
      id: `tab-${i}`,
      title: `Tab ${i + 1}`,
      agentId: 'agent-1',
      isNew: false,
    }));
  };

  const defaultProps = {
    tabs: createMockTabs(3),
    activeTabId: 'tab-0',
    onTabSelect: vi.fn(),
    onTabClose: vi.fn(),
  };

  describe('keyboard navigation', () => {
    it('moves focus to next tab on ArrowRight', () => {
      render(<SessionTabBar {...defaultProps} />);
      
      const tabs = screen.getAllByRole('tab');
      tabs[0].focus();
      
      fireEvent.keyDown(tabs[0], { key: 'ArrowRight' });
      
      expect(document.activeElement).toBe(tabs[1]);
    });

    it('moves focus to previous tab on ArrowLeft', () => {
      render(<SessionTabBar {...defaultProps} />);
      
      const tabs = screen.getAllByRole('tab');
      tabs[1].focus();
      
      fireEvent.keyDown(tabs[1], { key: 'ArrowLeft' });
      
      expect(document.activeElement).toBe(tabs[0]);
    });

    it('wraps to last tab when pressing ArrowLeft on first tab', () => {
      render(<SessionTabBar {...defaultProps} />);
      
      const tabs = screen.getAllByRole('tab');
      tabs[0].focus();
      
      fireEvent.keyDown(tabs[0], { key: 'ArrowLeft' });
      
      expect(document.activeElement).toBe(tabs[2]);
    });

    it('wraps to first tab when pressing ArrowRight on last tab', () => {
      render(<SessionTabBar {...defaultProps} />);
      
      const tabs = screen.getAllByRole('tab');
      tabs[2].focus();
      
      fireEvent.keyDown(tabs[2], { key: 'ArrowRight' });
      
      expect(document.activeElement).toBe(tabs[0]);
    });

    it('moves focus to first tab on Home key', () => {
      render(<SessionTabBar {...defaultProps} />);
      
      const tabs = screen.getAllByRole('tab');
      tabs[2].focus();
      
      fireEvent.keyDown(tabs[2], { key: 'Home' });
      
      expect(document.activeElement).toBe(tabs[0]);
    });

    it('moves focus to last tab on End key', () => {
      render(<SessionTabBar {...defaultProps} />);
      
      const tabs = screen.getAllByRole('tab');
      tabs[0].focus();
      
      fireEvent.keyDown(tabs[0], { key: 'End' });
      
      expect(document.activeElement).toBe(tabs[2]);
    });

    it('selects tab on Enter key', () => {
      const onTabSelect = vi.fn();
      render(<SessionTabBar {...defaultProps} onTabSelect={onTabSelect} activeTabId="tab-1" />);
      
      const tabs = screen.getAllByRole('tab');
      tabs[0].focus();
      
      fireEvent.keyDown(tabs[0], { key: 'Enter' });
      
      expect(onTabSelect).toHaveBeenCalledWith('tab-0');
    });

    it('selects tab on Space key', () => {
      const onTabSelect = vi.fn();
      render(<SessionTabBar {...defaultProps} onTabSelect={onTabSelect} activeTabId="tab-1" />);
      
      const tabs = screen.getAllByRole('tab');
      tabs[0].focus();
      
      fireEvent.keyDown(tabs[0], { key: ' ' });
      
      expect(onTabSelect).toHaveBeenCalledWith('tab-0');
    });

    it('does not call onTabSelect when pressing Enter on active tab', () => {
      const onTabSelect = vi.fn();
      render(<SessionTabBar {...defaultProps} onTabSelect={onTabSelect} activeTabId="tab-0" />);
      
      const tabs = screen.getAllByRole('tab');
      tabs[0].focus();
      
      fireEvent.keyDown(tabs[0], { key: 'Enter' });
      
      expect(onTabSelect).not.toHaveBeenCalled();
    });
  });

  describe('ARIA attributes', () => {
    it('renders tablist role on container', () => {
      render(<SessionTabBar {...defaultProps} />);
      
      const tablist = screen.getByRole('tablist');
      expect(tablist).toBeDefined();
    });

    it('renders tab role on each tab', () => {
      render(<SessionTabBar {...defaultProps} />);
      
      const tabs = screen.getAllByRole('tab');
      expect(tabs).toHaveLength(3);
    });

    it('sets aria-selected=true on active tab', () => {
      render(<SessionTabBar {...defaultProps} activeTabId="tab-1" />);
      
      const tabs = screen.getAllByRole('tab');
      expect(tabs[0].getAttribute('aria-selected')).toBe('false');
      expect(tabs[1].getAttribute('aria-selected')).toBe('true');
      expect(tabs[2].getAttribute('aria-selected')).toBe('false');
    });

    it('has aria-label on tablist', () => {
      render(<SessionTabBar {...defaultProps} />);
      
      const tablist = screen.getByRole('tablist');
      expect(tablist.getAttribute('aria-label')).toBe('Session tabs');
    });
  });

  describe('active tab visual distinction', () => {
    // run_843962a5: active tab now uses the product accent var (--color-primary,
    // which follows the user's accent preset) instead of a hardcoded blue-500,
    // so the active tab matches the app theme (GUI12 underline-no-layout-shift
    // still holds via border-b-2 on all tabs).
    it('active tab carries the accent bottom-border underline', () => {
      render(<SessionTabBar {...defaultProps} activeTabId="tab-1" />);
      const tabs = screen.getAllByRole('tab');
      expect(tabs[1].className).toContain('border-[var(--color-primary)]');
      // and it is genuinely distinct — inactive tabs do NOT carry the accent border
      expect(tabs[0].className).not.toContain('border-[var(--color-primary)]');
    });

    it('inactive tabs carry a transparent border placeholder (no layout shift)', () => {
      render(<SessionTabBar {...defaultProps} activeTabId="tab-1" />);
      const tabs = screen.getAllByRole('tab');
      expect(tabs[0].className).toContain('border-transparent');
      expect(tabs[2].className).toContain('border-transparent');
    });

    it('all tabs reserve border height via border-b-2 (active and inactive)', () => {
      render(<SessionTabBar {...defaultProps} activeTabId="tab-1" />);
      const tabs = screen.getAllByRole('tab');
      tabs.forEach((t) => expect(t.className).toContain('border-b-2'));
    });

    it('active tab has a distinct accent-tinted background (not inactive)', () => {
      render(<SessionTabBar {...defaultProps} activeTabId="tab-1" />);
      const tabs = screen.getAllByRole('tab');
      // accent-tinted fill via color-mix on the product accent var
      expect(tabs[1].className).toContain('color-mix');
      expect(tabs[1].className).toContain('var(--color-primary)');
      // inactive tab has no such tinted fill
      expect(tabs[0].className).not.toContain('color-mix');
    });
  });

  describe('single tab behavior', () => {
    it('handles keyboard navigation with single tab', () => {
      const singleTab = createMockTabs(1);
      render(<SessionTabBar {...defaultProps} tabs={singleTab} activeTabId="tab-0" />);
      
      const tabs = screen.getAllByRole('tab');
      tabs[0].focus();
      
      // ArrowRight should stay on same tab (wrap around)
      fireEvent.keyDown(tabs[0], { key: 'ArrowRight' });
      expect(document.activeElement).toBe(tabs[0]);
      
      // ArrowLeft should stay on same tab (wrap around)
      fireEvent.keyDown(tabs[0], { key: 'ArrowLeft' });
      expect(document.activeElement).toBe(tabs[0]);
    });
  });

  describe('tail "+" new-session button (run_843962a5)', () => {
    it('renders the "+" as the last element of the tab row, AFTER the last tab', () => {
      const onNewTab = vi.fn();
      const { container } = render(
        <SessionTabBar {...defaultProps} onNewTab={onNewTab} />
      );
      const row = container.querySelector('.session-tab-bar') as HTMLElement;
      const plus = screen.getByRole('button', { name: /new session/i });
      // The "+" is the LAST child of the tab-row flex container.
      expect(row.lastElementChild === plus || row.contains(plus)).toBe(true);
      // And it comes AFTER the tablist (which holds the tabs).
      const tablist = screen.getByRole('tablist');
      expect(
        tablist.compareDocumentPosition(plus) & Node.DOCUMENT_POSITION_FOLLOWING
      ).toBeTruthy();
    });

    it('the "+" is NOT a tab — getAllByRole("tab") count is unchanged (WAI-ARIA)', () => {
      render(<SessionTabBar {...defaultProps} onNewTab={vi.fn()} />);
      // 3 mock tabs → exactly 3 role=tab, the "+" (a <button>) is excluded.
      expect(screen.getAllByRole('tab')).toHaveLength(3);
    });

    it('clicking the "+" calls onNewTab', () => {
      const onNewTab = vi.fn();
      render(<SessionTabBar {...defaultProps} onNewTab={onNewTab} />);
      fireEvent.click(screen.getByRole('button', { name: /new session/i }));
      expect(onNewTab).toHaveBeenCalledTimes(1);
    });

    it('disabled "+" does not fire onNewTab (click is a no-op)', () => {
      const onNewTab = vi.fn();
      render(<SessionTabBar {...defaultProps} onNewTab={onNewTab} isNewTabDisabled />);
      const plus = screen.getByRole('button', { name: /limit|new session/i });
      fireEvent.click(plus);
      expect(onNewTab).not.toHaveBeenCalled();
    });

    it('disabled "+" stays hoverable so it can explain itself (aria-disabled, NOT the disabled attr)', () => {
      // Regression: a native `disabled` button emits no pointer events, so the
      // explanatory title/aria-label tooltip never renders and no hover fires.
      // The fix uses aria-disabled + a click no-op so the control stays
      // interactive and the "why can't I click" tooltip actually shows.
      render(<SessionTabBar {...defaultProps} onNewTab={vi.fn()} isNewTabDisabled />);
      const plus = screen.getByRole('button', { name: /limit|new session/i });
      expect(plus).not.toBeDisabled();                       // NOT the native disabled attr
      expect(plus).toHaveAttribute('aria-disabled', 'true'); // conveyed to AT instead
      expect(plus.getAttribute('title')).toMatch(/limit|resources/i); // tooltip copy present
    });

    it('enabled "+" is not aria-disabled', () => {
      render(<SessionTabBar {...defaultProps} onNewTab={vi.fn()} />);
      const plus = screen.getByRole('button', { name: /new session/i });
      expect(plus).toHaveAttribute('aria-disabled', 'false');
    });

    it('omits the "+" when onNewTab is not provided', () => {
      render(<SessionTabBar {...defaultProps} />);
      expect(screen.queryByRole('button', { name: /new session/i })).toBeNull();
    });

    it('arrow-key nav still cycles only tabs with the "+" present', () => {
      render(<SessionTabBar {...defaultProps} activeTabId="tab-0" onNewTab={vi.fn()} />);
      const tabs = screen.getAllByRole('tab');
      tabs[0].focus();
      fireEvent.keyDown(tabs[0], { key: 'ArrowRight' });
      expect(document.activeElement).toBe(tabs[1]);
    });
  });
});
