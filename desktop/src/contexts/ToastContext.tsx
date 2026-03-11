/**
 * Toast notification context and provider.
 *
 * Provides a centralized toast notification system accessible via the
 * `useToast()` hook from any component in the tree. Manages a queue of
 * toast notifications with the following behaviors:
 *
 * - **Max 5 visible** — additional toasts are queued and promoted as
 *   visible toasts are dismissed or auto-expire.
 * - **Auto-dismiss** — success/info toasts auto-dismiss after 5 s by
 *   default; warning/error toasts persist until manually dismissed
 *   (unless `autoDismiss: true` is set explicitly).
 * - **Deduplication** — toasts with the same `id` replace existing ones
 *   rather than stacking.
 * - **Actionable toasts** — optional `action` field renders a clickable
 *   button inside the toast.
 *
 * Key exports:
 * - `ToastProvider`  — wraps the app at root level
 * - `useToast`       — hook returning `{ addToast, removeToast, toasts }`
 */

import {
  createContext,
  useContext,
  useCallback,
  useRef,
  useState,
  useEffect,
  type ReactNode,
} from 'react';
import type { ToastOptions, ToastItem, ToastSeverity } from '../types';

/** Maximum number of toasts visible at once. */
const MAX_VISIBLE = 5;

/** Default auto-dismiss duration in milliseconds. */
const DEFAULT_DURATION_MS = 5000;

/** Severities that auto-dismiss by default. */
const AUTO_DISMISS_SEVERITIES: Set<ToastSeverity> = new Set(['success', 'info']);

// ---------------------------------------------------------------------------
// Context value interface
// ---------------------------------------------------------------------------

interface ToastContextValue {
  /** Add a toast. Returns the toast id (generated if not provided). */
  addToast: (options: ToastOptions) => string;
  /** Remove a toast by id. */
  removeToast: (id: string) => void;
  /** Currently visible toasts (max 5). */
  toasts: ToastItem[];
}

const ToastContext = createContext<ToastContextValue | undefined>(undefined);

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

interface ToastProviderProps {
  children: ReactNode;
}

export function ToastProvider({ children }: ToastProviderProps) {
  // All toasts (visible + queued). Visible = first MAX_VISIBLE items.
  const [allToasts, setAllToasts] = useState<ToastItem[]>([]);

  // Track active auto-dismiss timers so we can clear them on removal.
  const timersRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());

  // ------- helpers -------

  /** Determine whether a toast should auto-dismiss. */
  const shouldAutoDismiss = useCallback((opts: ToastOptions): boolean => {
    if (opts.autoDismiss !== undefined) return opts.autoDismiss;
    return AUTO_DISMISS_SEVERITIES.has(opts.severity);
  }, []);

  /** Schedule auto-dismiss for a toast if applicable. */
  const scheduleAutoDismiss = useCallback(
    (id: string, opts: ToastOptions) => {
      if (!shouldAutoDismiss(opts)) return;
      const duration = opts.durationMs ?? DEFAULT_DURATION_MS;
      const timer = setTimeout(() => {
        timersRef.current.delete(id);
        setAllToasts((prev) => prev.filter((t) => t.id !== id));
      }, duration);
      timersRef.current.set(id, timer);
    },
    [shouldAutoDismiss],
  );

  // ------- public API -------

  const removeToast = useCallback((id: string) => {
    const timer = timersRef.current.get(id);
    if (timer) {
      clearTimeout(timer);
      timersRef.current.delete(id);
    }
    setAllToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const addToast = useCallback(
    (options: ToastOptions): string => {
      const id = options.id ?? crypto.randomUUID();
      const item: ToastItem = {
        ...options,
        id,
        createdAt: Date.now(),
      };

      setAllToasts((prev) => {
        // Deduplication: replace existing toast with same id
        const existingIdx = prev.findIndex((t) => t.id === id);
        if (existingIdx !== -1) {
          // Clear old timer if any
          const oldTimer = timersRef.current.get(id);
          if (oldTimer) {
            clearTimeout(oldTimer);
            timersRef.current.delete(id);
          }
          const next = [...prev];
          next[existingIdx] = item;
          return next;
        }
        return [...prev, item];
      });

      // Schedule auto-dismiss (runs after state update)
      scheduleAutoDismiss(id, options);

      return id;
    },
    [scheduleAutoDismiss],
  );

  // Expose only the first MAX_VISIBLE toasts as "visible".
  const visibleToasts = allToasts.slice(0, MAX_VISIBLE);

  // Clean up all timers on unmount.
  useEffect(() => {
    return () => {
      timersRef.current.forEach((timer) => clearTimeout(timer));
      timersRef.current.clear();
    };
  }, []);

  return (
    <ToastContext.Provider
      value={{ addToast, removeToast, toasts: visibleToasts }}
    >
      {children}
    </ToastContext.Provider>
  );
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useToast(): ToastContextValue {
  const context = useContext(ToastContext);
  if (context === undefined) {
    throw new Error('useToast must be used within a ToastProvider');
  }
  return context;
}
