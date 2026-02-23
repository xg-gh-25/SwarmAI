import { useState, useEffect, useCallback, useMemo } from 'react';
import {
  RIGHT_SIDEBAR_IDS,
  type RightSidebarId,
  type SidebarWidthConfig,
} from '../pages/chat/constants';

interface UseRightSidebarGroupOptions {
  defaultActive: RightSidebarId;
  widthConfigs: Record<RightSidebarId, SidebarWidthConfig>;
}

interface SidebarWidthState {
  width: number;
  isResizing: boolean;
  handleMouseDown: (e: React.MouseEvent) => void;
}

interface UseRightSidebarGroupReturn {
  /** Currently active sidebar */
  activeSidebar: RightSidebarId;

  /** Open a specific sidebar (closes others). No-op if already active. */
  openSidebar: (id: RightSidebarId) => void;

  /** Check if a specific sidebar is active */
  isActive: (id: RightSidebarId) => boolean;

  /** Width and resize state for each sidebar */
  widths: Record<RightSidebarId, SidebarWidthState>;
}

/**
 * Custom hook for managing right sidebar group with mutual exclusion.
 * Only one sidebar can be visible at a time.
 * Width preferences are persisted to localStorage, but visibility state is ephemeral.
 */
export function useRightSidebarGroup(
  options: UseRightSidebarGroupOptions
): UseRightSidebarGroupReturn {
  const { defaultActive, widthConfigs } = options;

  // Active sidebar state - always starts with defaultActive (no persistence)
  const [activeSidebar, setActiveSidebar] = useState<RightSidebarId>(defaultActive);

  // Width states for each sidebar
  const [widths, setWidths] = useState<Record<RightSidebarId, number>>(() => {
    const initialWidths = {} as Record<RightSidebarId, number>;
    for (const id of RIGHT_SIDEBAR_IDS) {
      const config = widthConfigs[id];
      const saved = localStorage.getItem(config.storageKey);
      initialWidths[id] = saved ? parseInt(saved, 10) : config.defaultWidth;
    }
    return initialWidths;
  });

  // Track which sidebar is currently being resized
  const [resizingSidebar, setResizingSidebar] = useState<RightSidebarId | null>(null);

  // Clean up old localStorage keys on mount (one-time migration)
  useEffect(() => {
    localStorage.removeItem('chatSidebarCollapsed');
    localStorage.removeItem('rightSidebarCollapsed');
    localStorage.removeItem('todoRadarSidebarCollapsed');
  }, []);

  // Persist width changes to localStorage
  useEffect(() => {
    for (const id of RIGHT_SIDEBAR_IDS) {
      const config = widthConfigs[id];
      localStorage.setItem(config.storageKey, String(widths[id]));
    }
  }, [widths, widthConfigs]);

  // Handle resize mouse events
  useEffect(() => {
    if (!resizingSidebar) return;

    const config = widthConfigs[resizingSidebar];

    const handleMouseMove = (e: MouseEvent) => {
      // Right sidebars calculate width from right edge
      const newWidth = window.innerWidth - e.clientX;

      if (newWidth >= config.minWidth && newWidth <= config.maxWidth) {
        setWidths((prev) => ({
          ...prev,
          [resizingSidebar]: newWidth,
        }));
      }
    };

    const handleMouseUp = () => {
      setResizingSidebar(null);
    };

    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
    document.body.style.cursor = 'ew-resize';
    document.body.style.userSelect = 'none';

    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
  }, [resizingSidebar, widthConfigs]);

  // Open a specific sidebar (no-op if already active)
  const openSidebar = useCallback((id: RightSidebarId) => {
    // Validate sidebar ID
    if (!RIGHT_SIDEBAR_IDS.includes(id)) {
      console.warn(`Invalid sidebar ID: ${id}`);
      return;
    }

    // No-op if already active
    setActiveSidebar((current) => {
      if (current === id) {
        return current;
      }
      return id;
    });
  }, []);

  // Check if a specific sidebar is active
  const isActive = useCallback(
    (id: RightSidebarId): boolean => {
      return activeSidebar === id;
    },
    [activeSidebar]
  );

  // Create width state objects for each sidebar
  const widthStates = useMemo(() => {
    const states = {} as Record<RightSidebarId, SidebarWidthState>;

    for (const id of RIGHT_SIDEBAR_IDS) {
      states[id] = {
        width: widths[id],
        isResizing: resizingSidebar === id,
        handleMouseDown: (e: React.MouseEvent) => {
          e.preventDefault();
          setResizingSidebar(id);
        },
      };
    }

    return states;
  }, [widths, resizingSidebar]);

  return {
    activeSidebar,
    openSidebar,
    isActive,
    widths: widthStates,
  };
}
