import { useRef, useCallback, useEffect } from 'react';
import type { OpenTab } from '../types';
import type { TabStatus } from '../../../hooks/useUnifiedTabState';
import { SessionTab } from './SessionTab';

interface SessionTabBarProps {
  tabs: OpenTab[];
  activeTabId: string | null;
  onTabSelect: (tabId: string) => void;
  onTabClose: (tabId: string) => void;
  tabStatuses?: Record<string, TabStatus>;
  maxTitleLength?: number;
}

/**
 * Horizontal scrollable container for session tabs.
 * Renders SessionTab components with smooth scrolling and custom scrollbar styling.
 * Supports keyboard navigation with arrow keys following WAI-ARIA tabs pattern.
 *
 * Validates: Requirements 1.2
 */
export function SessionTabBar({
  tabs,
  activeTabId,
  onTabSelect,
  onTabClose,
  tabStatuses,
  maxTitleLength = 25,
}: SessionTabBarProps) {
  const tabRefs = useRef<Map<string, HTMLDivElement>>(new Map());

  const setTabRef = useCallback((tabId: string, element: HTMLDivElement | null) => {
    if (element) {
      tabRefs.current.set(tabId, element);
    } else {
      tabRefs.current.delete(tabId);
    }
  }, []);

  const focusTab = useCallback((tabId: string) => {
    const tabElement = tabRefs.current.get(tabId);
    if (tabElement) {
      tabElement.focus();
      // Scroll the tab into view (guard for test environment)
      tabElement.scrollIntoView?.({ behavior: 'smooth', block: 'nearest', inline: 'nearest' });
    }
  }, []);

  // Auto-scroll the active tab into view on mount and when activeTabId changes.
  // Handles the app-restart scenario where the active tab (e.g. tab 6) is
  // off-screen in the scrollable tab bar after restore from open_tabs.json.
  useEffect(() => {
    if (!activeTabId) return;
    // Use rAF to ensure the DOM has laid out before scrolling
    requestAnimationFrame(() => {
      const el = tabRefs.current.get(activeTabId);
      el?.scrollIntoView?.({ behavior: 'smooth', block: 'nearest', inline: 'nearest' });
    });
  }, [activeTabId]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent, currentTabId: string) => {
    const currentIndex = tabs.findIndex(tab => tab.id === currentTabId);
    if (currentIndex === -1) return;

    let targetIndex: number | null = null;

    switch (e.key) {
      case 'ArrowLeft':
        e.preventDefault();
        // Move to previous tab, wrap to end if at first
        targetIndex = currentIndex > 0 ? currentIndex - 1 : tabs.length - 1;
        break;
      case 'ArrowRight':
        e.preventDefault();
        // Move to next tab, wrap to start if at last
        targetIndex = currentIndex < tabs.length - 1 ? currentIndex + 1 : 0;
        break;
      case 'Home':
        e.preventDefault();
        // Move to first tab
        targetIndex = 0;
        break;
      case 'End':
        e.preventDefault();
        // Move to last tab
        targetIndex = tabs.length - 1;
        break;
      default:
        return;
    }

    if (targetIndex !== null && tabs[targetIndex]) {
      focusTab(tabs[targetIndex].id);
    }
  }, [tabs, focusTab]);

  return (
    <div
      role="tablist"
      aria-label="Session tabs"
      className="session-tab-bar flex items-center gap-1 flex-1 min-w-0 overflow-x-auto"
      style={{
        scrollBehavior: 'smooth',
        scrollbarWidth: 'thin',
      }}
    >
      {tabs.map((tab, index) => (
        <SessionTab
          key={tab.id}
          tab={tab}
          index={index}
          isActive={tab.id === activeTabId}
          onSelect={onTabSelect}
          onClose={onTabClose}
          status={tabStatuses?.[tab.id]}
          maxTitleLength={maxTitleLength}
          onKeyDown={(e) => handleKeyDown(e, tab.id)}
          ref={(el) => setTabRef(tab.id, el)}
        />
      ))}

      <style>{`
        .session-tab-bar::-webkit-scrollbar {
          height: 4px;
        }
        .session-tab-bar::-webkit-scrollbar-track {
          background: transparent;
        }
        .session-tab-bar::-webkit-scrollbar-thumb {
          background: var(--color-border);
          border-radius: 2px;
        }
        .session-tab-bar::-webkit-scrollbar-thumb:hover {
          background: var(--color-text-muted);
        }
      `}</style>
    </div>
  );
}
