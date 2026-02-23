/**
 * Unit Tests for useRightSidebarGroup Hook
 *
 * **Feature: right-sidebar-mutual-exclusion**
 * **Validates: Requirements 1.1, 1.4, 2.2, 3.1, 4.2**
 *
 * These tests validate the useRightSidebarGroup custom hook that manages
 * mutual exclusion for right sidebars (TodoRadar, ChatHistory, FileBrowser).
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useRightSidebarGroup } from './useRightSidebarGroup';
import { RIGHT_SIDEBAR_WIDTH_CONFIGS, type RightSidebarId } from '../pages/chat/constants';

// ============== Test Setup ==============

// Mock localStorage
class MockLocalStorage {
  private store: Map<string, string> = new Map();

  getItem(key: string): string | null {
    return this.store.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    this.store.set(key, value);
  }

  removeItem(key: string): void {
    this.store.delete(key);
  }

  clear(): void {
    this.store.clear();
  }

  get length(): number {
    return this.store.size;
  }

  key(index: number): string | null {
    const keys = Array.from(this.store.keys());
    return keys[index] ?? null;
  }
}

let originalLocalStorage: Storage;
let mockStorage: MockLocalStorage;

// ============== Unit Tests ==============

describe('useRightSidebarGroup Hook - Unit Tests', () => {
  beforeEach(() => {
    originalLocalStorage = window.localStorage;
    mockStorage = new MockLocalStorage();
    Object.defineProperty(window, 'localStorage', {
      value: mockStorage,
      writable: true,
    });
  });

  afterEach(() => {
    Object.defineProperty(window, 'localStorage', {
      value: originalLocalStorage,
      writable: true,
    });
    mockStorage.clear();
  });

  /**
   * Test: Initial state defaults to TodoRadarSidebar
   * **Validates: Requirements 3.1**
   */
  describe('Initial State', () => {
    it('should initialize with TodoRadarSidebar as active', () => {
      const { result } = renderHook(() =>
        useRightSidebarGroup({
          defaultActive: 'todoRadar',
          widthConfigs: RIGHT_SIDEBAR_WIDTH_CONFIGS,
        })
      );

      expect(result.current.activeSidebar).toBe('todoRadar');
      expect(result.current.isActive('todoRadar')).toBe(true);
      expect(result.current.isActive('chatHistory')).toBe(false);
      expect(result.current.isActive('fileBrowser')).toBe(false);
    });

    it('should initialize with specified defaultActive sidebar', () => {
      const { result } = renderHook(() =>
        useRightSidebarGroup({
          defaultActive: 'chatHistory',
          widthConfigs: RIGHT_SIDEBAR_WIDTH_CONFIGS,
        })
      );

      expect(result.current.activeSidebar).toBe('chatHistory');
      expect(result.current.isActive('chatHistory')).toBe(true);
      expect(result.current.isActive('todoRadar')).toBe(false);
      expect(result.current.isActive('fileBrowser')).toBe(false);
    });

    it('should initialize with fileBrowser as active when specified', () => {
      const { result } = renderHook(() =>
        useRightSidebarGroup({
          defaultActive: 'fileBrowser',
          widthConfigs: RIGHT_SIDEBAR_WIDTH_CONFIGS,
        })
      );

      expect(result.current.activeSidebar).toBe('fileBrowser');
      expect(result.current.isActive('fileBrowser')).toBe(true);
      expect(result.current.isActive('todoRadar')).toBe(false);
      expect(result.current.isActive('chatHistory')).toBe(false);
    });
  });

  /**
   * Test: localStorage is ignored for visibility state
   * **Validates: Requirements 4.2**
   */
  describe('localStorage Visibility State Ignored', () => {
    it('should ignore localStorage for initial visibility state', () => {
      // Set old localStorage keys that should be ignored
      mockStorage.setItem('chatSidebarCollapsed', 'false');
      mockStorage.setItem('todoRadarSidebarCollapsed', 'true');
      mockStorage.setItem('rightSidebarCollapsed', 'false');

      const { result } = renderHook(() =>
        useRightSidebarGroup({
          defaultActive: 'todoRadar',
          widthConfigs: RIGHT_SIDEBAR_WIDTH_CONFIGS,
        })
      );

      // Should still default to todoRadar regardless of localStorage
      expect(result.current.activeSidebar).toBe('todoRadar');
    });

    it('should clean up old localStorage collapsed state keys on mount', () => {
      // Set old localStorage keys
      mockStorage.setItem('chatSidebarCollapsed', 'false');
      mockStorage.setItem('rightSidebarCollapsed', 'true');
      mockStorage.setItem('todoRadarSidebarCollapsed', 'false');

      renderHook(() =>
        useRightSidebarGroup({
          defaultActive: 'todoRadar',
          widthConfigs: RIGHT_SIDEBAR_WIDTH_CONFIGS,
        })
      );

      // Old keys should be removed
      expect(mockStorage.getItem('chatSidebarCollapsed')).toBeNull();
      expect(mockStorage.getItem('rightSidebarCollapsed')).toBeNull();
      expect(mockStorage.getItem('todoRadarSidebarCollapsed')).toBeNull();
    });

    it('should not persist visibility state to localStorage', () => {
      const { result } = renderHook(() =>
        useRightSidebarGroup({
          defaultActive: 'todoRadar',
          widthConfigs: RIGHT_SIDEBAR_WIDTH_CONFIGS,
        })
      );

      // Switch sidebars
      act(() => {
        result.current.openSidebar('chatHistory');
      });

      // No collapsed state keys should be set
      expect(mockStorage.getItem('chatSidebarCollapsed')).toBeNull();
      expect(mockStorage.getItem('todoRadarSidebarCollapsed')).toBeNull();
      expect(mockStorage.getItem('rightSidebarCollapsed')).toBeNull();
    });
  });

  /**
   * Test: Width persistence still works
   * **Validates: Requirements 4.2 (width persistence maintained)**
   */
  describe('Width Persistence', () => {
    it('should persist and restore width values from localStorage', () => {
      mockStorage.setItem('todoRadarSidebarWidth', '400');

      const { result } = renderHook(() =>
        useRightSidebarGroup({
          defaultActive: 'todoRadar',
          widthConfigs: RIGHT_SIDEBAR_WIDTH_CONFIGS,
        })
      );

      expect(result.current.widths.todoRadar.width).toBe(400);
    });

    it('should restore width for all sidebars from localStorage', () => {
      mockStorage.setItem('todoRadarSidebarWidth', '350');
      mockStorage.setItem('chatSidebarWidth', '280');
      mockStorage.setItem('rightSidebarWidth', '400');

      const { result } = renderHook(() =>
        useRightSidebarGroup({
          defaultActive: 'todoRadar',
          widthConfigs: RIGHT_SIDEBAR_WIDTH_CONFIGS,
        })
      );

      expect(result.current.widths.todoRadar.width).toBe(350);
      expect(result.current.widths.chatHistory.width).toBe(280);
      expect(result.current.widths.fileBrowser.width).toBe(400);
    });

    it('should use default width when localStorage is empty', () => {
      const { result } = renderHook(() =>
        useRightSidebarGroup({
          defaultActive: 'todoRadar',
          widthConfigs: RIGHT_SIDEBAR_WIDTH_CONFIGS,
        })
      );

      expect(result.current.widths.todoRadar.width).toBe(
        RIGHT_SIDEBAR_WIDTH_CONFIGS.todoRadar.defaultWidth
      );
      expect(result.current.widths.chatHistory.width).toBe(
        RIGHT_SIDEBAR_WIDTH_CONFIGS.chatHistory.defaultWidth
      );
      expect(result.current.widths.fileBrowser.width).toBe(
        RIGHT_SIDEBAR_WIDTH_CONFIGS.fileBrowser.defaultWidth
      );
    });

    it('should persist width changes to localStorage', () => {
      const { result: _result } = renderHook(() =>
        useRightSidebarGroup({
          defaultActive: 'todoRadar',
          widthConfigs: RIGHT_SIDEBAR_WIDTH_CONFIGS,
        })
      );

      // Width should be persisted on mount
      expect(mockStorage.getItem('todoRadarSidebarWidth')).toBe(
        String(RIGHT_SIDEBAR_WIDTH_CONFIGS.todoRadar.defaultWidth)
      );
      expect(mockStorage.getItem('chatSidebarWidth')).toBe(
        String(RIGHT_SIDEBAR_WIDTH_CONFIGS.chatHistory.defaultWidth)
      );
      expect(mockStorage.getItem('rightSidebarWidth')).toBe(
        String(RIGHT_SIDEBAR_WIDTH_CONFIGS.fileBrowser.defaultWidth)
      );
    });
  });

  /**
   * Test: Switching between sidebars
   * **Validates: Requirements 1.1, 1.4**
   */
  describe('Switching Between Sidebars', () => {
    it('should switch from todoRadar to chatHistory', () => {
      const { result } = renderHook(() =>
        useRightSidebarGroup({
          defaultActive: 'todoRadar',
          widthConfigs: RIGHT_SIDEBAR_WIDTH_CONFIGS,
        })
      );

      act(() => {
        result.current.openSidebar('chatHistory');
      });

      expect(result.current.activeSidebar).toBe('chatHistory');
      expect(result.current.isActive('chatHistory')).toBe(true);
      expect(result.current.isActive('todoRadar')).toBe(false);
      expect(result.current.isActive('fileBrowser')).toBe(false);
    });

    it('should switch from todoRadar to fileBrowser', () => {
      const { result } = renderHook(() =>
        useRightSidebarGroup({
          defaultActive: 'todoRadar',
          widthConfigs: RIGHT_SIDEBAR_WIDTH_CONFIGS,
        })
      );

      act(() => {
        result.current.openSidebar('fileBrowser');
      });

      expect(result.current.activeSidebar).toBe('fileBrowser');
      expect(result.current.isActive('fileBrowser')).toBe(true);
      expect(result.current.isActive('todoRadar')).toBe(false);
      expect(result.current.isActive('chatHistory')).toBe(false);
    });

    it('should switch from chatHistory to fileBrowser', () => {
      const { result } = renderHook(() =>
        useRightSidebarGroup({
          defaultActive: 'chatHistory',
          widthConfigs: RIGHT_SIDEBAR_WIDTH_CONFIGS,
        })
      );

      act(() => {
        result.current.openSidebar('fileBrowser');
      });

      expect(result.current.activeSidebar).toBe('fileBrowser');
      expect(result.current.isActive('fileBrowser')).toBe(true);
      expect(result.current.isActive('chatHistory')).toBe(false);
    });

    it('should handle multiple sidebar switches', () => {
      const { result } = renderHook(() =>
        useRightSidebarGroup({
          defaultActive: 'todoRadar',
          widthConfigs: RIGHT_SIDEBAR_WIDTH_CONFIGS,
        })
      );

      // Switch to chatHistory
      act(() => {
        result.current.openSidebar('chatHistory');
      });
      expect(result.current.activeSidebar).toBe('chatHistory');

      // Switch to fileBrowser
      act(() => {
        result.current.openSidebar('fileBrowser');
      });
      expect(result.current.activeSidebar).toBe('fileBrowser');

      // Switch back to todoRadar
      act(() => {
        result.current.openSidebar('todoRadar');
      });
      expect(result.current.activeSidebar).toBe('todoRadar');
    });

    it('should maintain exactly one active sidebar after any switch', () => {
      const { result } = renderHook(() =>
        useRightSidebarGroup({
          defaultActive: 'todoRadar',
          widthConfigs: RIGHT_SIDEBAR_WIDTH_CONFIGS,
        })
      );

      const sidebarIds: RightSidebarId[] = ['todoRadar', 'chatHistory', 'fileBrowser'];

      for (const targetSidebar of sidebarIds) {
        act(() => {
          result.current.openSidebar(targetSidebar);
        });

        // Count active sidebars
        const activeCount = sidebarIds.filter((id) => result.current.isActive(id)).length;
        expect(activeCount).toBe(1);
        expect(result.current.isActive(targetSidebar)).toBe(true);
      }
    });
  });

  /**
   * Test: No-op when clicking active sidebar button
   * **Validates: Requirements 2.2**
   */
  describe('No-op on Active Sidebar Click', () => {
    it('should not change state when clicking active todoRadar button', () => {
      const { result } = renderHook(() =>
        useRightSidebarGroup({
          defaultActive: 'todoRadar',
          widthConfigs: RIGHT_SIDEBAR_WIDTH_CONFIGS,
        })
      );

      const initialState = result.current.activeSidebar;

      act(() => {
        result.current.openSidebar('todoRadar');
      });

      expect(result.current.activeSidebar).toBe(initialState);
      expect(result.current.activeSidebar).toBe('todoRadar');
    });

    it('should not change state when clicking active chatHistory button', () => {
      const { result } = renderHook(() =>
        useRightSidebarGroup({
          defaultActive: 'chatHistory',
          widthConfigs: RIGHT_SIDEBAR_WIDTH_CONFIGS,
        })
      );

      act(() => {
        result.current.openSidebar('chatHistory');
      });

      expect(result.current.activeSidebar).toBe('chatHistory');
    });

    it('should not change state when clicking active fileBrowser button', () => {
      const { result } = renderHook(() =>
        useRightSidebarGroup({
          defaultActive: 'fileBrowser',
          widthConfigs: RIGHT_SIDEBAR_WIDTH_CONFIGS,
        })
      );

      act(() => {
        result.current.openSidebar('fileBrowser');
      });

      expect(result.current.activeSidebar).toBe('fileBrowser');
    });

    it('should keep sidebar open after multiple clicks on same button', () => {
      const { result } = renderHook(() =>
        useRightSidebarGroup({
          defaultActive: 'todoRadar',
          widthConfigs: RIGHT_SIDEBAR_WIDTH_CONFIGS,
        })
      );

      // Click the same button multiple times
      act(() => {
        result.current.openSidebar('todoRadar');
        result.current.openSidebar('todoRadar');
        result.current.openSidebar('todoRadar');
      });

      expect(result.current.activeSidebar).toBe('todoRadar');
      expect(result.current.isActive('todoRadar')).toBe(true);
    });
  });

  /**
   * Test: Invalid sidebar ID handling
   */
  describe('Invalid Sidebar ID Handling', () => {
    it('should ignore invalid sidebar ID and log warning', () => {
      const consoleSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});

      const { result } = renderHook(() =>
        useRightSidebarGroup({
          defaultActive: 'todoRadar',
          widthConfigs: RIGHT_SIDEBAR_WIDTH_CONFIGS,
        })
      );

      act(() => {
        // @ts-expect-error - Testing invalid input
        result.current.openSidebar('invalidSidebar');
      });

      expect(consoleSpy).toHaveBeenCalledWith('Invalid sidebar ID: invalidSidebar');
      expect(result.current.activeSidebar).toBe('todoRadar');

      consoleSpy.mockRestore();
    });
  });

  /**
   * Test: Width state structure
   */
  describe('Width State Structure', () => {
    it('should provide width state for all sidebars', () => {
      const { result } = renderHook(() =>
        useRightSidebarGroup({
          defaultActive: 'todoRadar',
          widthConfigs: RIGHT_SIDEBAR_WIDTH_CONFIGS,
        })
      );

      // Check that all sidebars have width state
      expect(result.current.widths.todoRadar).toBeDefined();
      expect(result.current.widths.chatHistory).toBeDefined();
      expect(result.current.widths.fileBrowser).toBeDefined();

      // Check structure of width state
      expect(typeof result.current.widths.todoRadar.width).toBe('number');
      expect(typeof result.current.widths.todoRadar.isResizing).toBe('boolean');
      expect(typeof result.current.widths.todoRadar.handleMouseDown).toBe('function');
    });

    it('should initialize isResizing to false for all sidebars', () => {
      const { result } = renderHook(() =>
        useRightSidebarGroup({
          defaultActive: 'todoRadar',
          widthConfigs: RIGHT_SIDEBAR_WIDTH_CONFIGS,
        })
      );

      expect(result.current.widths.todoRadar.isResizing).toBe(false);
      expect(result.current.widths.chatHistory.isResizing).toBe(false);
      expect(result.current.widths.fileBrowser.isResizing).toBe(false);
    });
  });
});
