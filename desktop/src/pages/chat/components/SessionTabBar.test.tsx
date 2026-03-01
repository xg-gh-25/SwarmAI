import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { SessionTabBar } from './SessionTabBar';
import type { OpenTab } from '../types';

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
});
