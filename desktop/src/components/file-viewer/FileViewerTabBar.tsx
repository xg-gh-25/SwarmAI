/**
 * FileViewerTabBar — Horizontal tab strip for the unified FileViewer.
 *
 * Renders one tab per open file with icon, truncated name, dirty indicator,
 * and close button. Supports horizontal scroll overflow, middle-click close,
 * and CSS-variable theming.
 */

import { useCallback } from 'react';
import type { FileTab } from './hooks/useFileViewerTabs';
import { getFileTypeInfo } from './utils/fileViewTypes';

export interface FileViewerTabBarProps {
  tabs: FileTab[];
  activeTabId: string | null;
  onSwitch: (tabId: string) => void;
  onClose: (tabId: string) => void;
}

export default function FileViewerTabBar({
  tabs,
  activeTabId,
  onSwitch,
  onClose,
}: FileViewerTabBarProps) {
  /** Middle-click (button === 1) closes the tab. */
  const handleMouseDown = useCallback(
    (e: React.MouseEvent, tabId: string) => {
      if (e.button === 1) {
        e.preventDefault();
        onClose(tabId);
      }
    },
    [onClose],
  );

  if (tabs.length === 0) return null;

  return (
    <div
      className="flex items-end overflow-x-auto border-b border-[var(--color-border)]"
      style={{
        scrollbarWidth: 'none',       /* Firefox */
        msOverflowStyle: 'none',      /* IE/Edge */
      }}
    >
      {/* Hide Webkit scrollbar via inline style sheet — avoids a global CSS file */}
      <style>{`.fv-tab-bar::-webkit-scrollbar { display: none; }`}</style>

      <div className="fv-tab-bar flex items-end overflow-x-auto w-full">
        {tabs.map((tab) => {
          const isActive = tab.id === activeTabId;
          const info = getFileTypeInfo(tab.fileName);

          return (
            <button
              key={tab.id}
              type="button"
              onClick={() => onSwitch(tab.id)}
              onMouseDown={(e) => handleMouseDown(e, tab.id)}
              title={tab.filePath}
              className={`
                group relative flex items-center gap-1.5 px-3 py-1.5
                text-xs whitespace-nowrap select-none shrink-0
                transition-colors duration-100
                border-b-2
                ${isActive
                  ? 'bg-[var(--color-bg)] text-[var(--color-text)] border-[var(--color-accent)]'
                  : 'bg-[var(--color-bg-secondary)] text-[var(--color-text-secondary)] border-transparent hover:bg-[var(--color-bg-hover)]'
                }
              `}
            >
              {/* File type icon */}
              <span
                className="material-symbols-outlined text-[14px] leading-none opacity-60"
                aria-hidden="true"
              >
                {info.icon}
              </span>

              {/* Dirty dot */}
              {tab.isDirty && (
                <span
                  className="w-1.5 h-1.5 rounded-full bg-orange-400 shrink-0"
                  title="Unsaved changes"
                />
              )}

              {/* File name (truncated) */}
              <span className="max-w-[140px] truncate">{tab.fileName}</span>

              {/* Close button — always visible when dirty, otherwise on hover */}
              <span
                role="button"
                tabIndex={-1}
                onClick={(e) => {
                  e.stopPropagation();
                  onClose(tab.id);
                }}
                className={`
                  ml-1 w-4 h-4 flex items-center justify-center rounded
                  text-[11px] leading-none
                  hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text)]
                  ${tab.isDirty ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'}
                  transition-opacity duration-100
                `}
                aria-label={`Close ${tab.fileName}`}
              >
                &times;
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
