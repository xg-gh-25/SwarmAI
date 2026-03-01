import { useState, useEffect, useCallback } from 'react';

interface SidebarConfig {
  storageKey: string;
  widthStorageKey: string;
  defaultCollapsed: boolean;
  defaultWidth: number;
  minWidth: number;
  maxWidth: number;
}

interface SidebarState {
  collapsed: boolean;
  width: number;
  isResizing: boolean;
  setCollapsed: (value: boolean) => void;
  toggle: () => void;
  setIsResizing: (value: boolean) => void;
  handleMouseDown: (e: React.MouseEvent) => void;
}

/**
 * Custom hook for managing sidebar state with localStorage persistence
 */
export function useSidebarState(config: SidebarConfig): SidebarState {
  const { storageKey, widthStorageKey, defaultCollapsed, defaultWidth, minWidth, maxWidth } = config;

  const [collapsed, setCollapsedState] = useState(() => {
    const saved = localStorage.getItem(storageKey);
    return saved !== null ? saved === 'true' : defaultCollapsed;
  });

  const [width, setWidth] = useState(() => {
    const saved = localStorage.getItem(widthStorageKey);
    return saved ? parseInt(saved, 10) : defaultWidth;
  });

  const [isResizing, setIsResizing] = useState(false);

  // Persist collapsed state
  useEffect(() => {
    localStorage.setItem(storageKey, String(collapsed));
  }, [collapsed, storageKey]);

  // Persist width
  useEffect(() => {
    localStorage.setItem(widthStorageKey, String(width));
  }, [width, widthStorageKey]);

  // Handle resize mouse events
  useEffect(() => {
    if (!isResizing) return;

    const handleMouseMove = (e: MouseEvent) => {
      const newWidth = storageKey.includes('right')
        ? window.innerWidth - e.clientX
        : e.clientX;

      if (newWidth >= minWidth && newWidth <= maxWidth) {
        setWidth(newWidth);
      }
    };

    const handleMouseUp = () => {
      setIsResizing(false);
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
  }, [isResizing, minWidth, maxWidth, storageKey]);

  const setCollapsed = useCallback((value: boolean) => {
    setCollapsedState(value);
  }, []);

  const toggle = useCallback(() => {
    setCollapsedState((prev) => !prev);
  }, []);

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    setIsResizing(true);
  }, []);

  return {
    collapsed,
    width,
    isResizing,
    setCollapsed,
    toggle,
    setIsResizing,
    handleMouseDown,
  };
}
