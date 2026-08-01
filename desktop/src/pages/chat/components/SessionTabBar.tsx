import { useRef, useCallback, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
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
  /** New-session "+" — rendered at the TAIL of the tab strip. Omit to hide it. */
  onNewTab?: () => void;
  /** Disables the tail "+" when the tab limit is reached. */
  isNewTabDisabled?: boolean;
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
  onNewTab,
  isNewTabDisabled,
}: SessionTabBarProps) {
  const { t } = useTranslation();
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
    // Flex row: [role=tablist scroll container with tabs] + [tail "+" button].
    // The "+" is a sibling of the tablist (NOT a role=tablist child — WAI-ARIA
    // keeps tablist children tabs-only), but lives inside the SAME horizontal
    // scroll container so it trails the last tab and scrolls with the strip.
    <div
      className="session-tab-bar flex items-center gap-1 flex-1 min-w-0 overflow-x-auto"
      style={{
        scrollBehavior: 'smooth',
        scrollbarWidth: 'thin',
      }}
    >
      <div role="tablist" aria-label="Session tabs" className="flex items-center gap-1">
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
      </div>

      {onNewTab && (
        <>
          {/* Divider — visually detach the "+" from the tab strip so it reads
              as a distinct action, not part of the tabs. */}
          <span
            aria-hidden="true"
            className="flex-shrink-0 self-center w-px h-4 mx-1.5 bg-[var(--color-border)]"
          />
          <button
            type="button"
            // aria-disabled (NOT the native `disabled` attr): a disabled button
            // emits no pointer events, so its explanatory title/hover never
            // renders. aria-disabled keeps it hoverable + AT-announced; the
            // onClick guard makes it a no-op when at the tab limit.
            onClick={isNewTabDisabled ? undefined : onNewTab}
            aria-disabled={!!isNewTabDisabled}
            aria-label={isNewTabDisabled
              ? t('chat.tabLimitReached', 'System resources are limited. Close a tab or free memory to open another.')
              : t('chat.newSession', 'New Session')}
            title={isNewTabDisabled
              ? t('chat.tabLimitReached', 'System resources are limited. Close a tab or free memory to open another.')
              : t('chat.newSession', 'New Session (⌘N)')}
            className={`flex-shrink-0 flex items-center justify-center w-7 h-7 rounded-lg border transition-colors ${
              isNewTabDisabled
                ? 'border-transparent text-[var(--color-text-disabled,var(--color-text-muted))] opacity-50 cursor-not-allowed'
                : 'border-[var(--color-border)] text-[var(--color-text-muted)] hover:border-[var(--color-primary)] hover:bg-[color-mix(in_srgb,var(--color-primary)_12%,transparent)] hover:text-[var(--color-primary)]'
            }`}
          >
            <span className="material-symbols-outlined text-[18px]">add</span>
          </button>
        </>
      )}

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
