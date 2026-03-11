/**
 * Toast notification stack renderer.
 *
 * Consumes the `useToast()` hook to display up to 5 visible toasts in a
 * fixed top-right overlay. Each toast renders with a severity-appropriate
 * icon and color scheme, an optional action button, and a dismiss (X)
 * button.
 *
 * Key behaviors:
 * - `ToastStack`  — renders the fixed-position container and maps over
 *   visible toasts
 * - `ToastEntry`  — individual toast with icon, message, action, dismiss
 * - Animations    — CSS slide-in from right on entry, fade-out on exit
 * - Accessibility — `role="alert"` and `aria-live="polite"` on each toast
 *
 * Validates: Requirements 5.2, 5.5
 */

import { useState, useCallback } from 'react';
import clsx from 'clsx';
import { useToast } from '../../contexts/ToastContext';
import type { ToastItem, ToastSeverity } from '../../types';

// ---------------------------------------------------------------------------
// Severity → icon mapping (Material Symbols)
// ---------------------------------------------------------------------------

const ICON_MAP: Record<ToastSeverity, string> = {
  success: 'check_circle',
  info: 'info',
  warning: 'warning',
  error: 'error',
};

// ---------------------------------------------------------------------------
// Severity → color mapping (matches existing Toast.tsx patterns)
// ---------------------------------------------------------------------------

const COLOR_MAP: Record<ToastSeverity, string> = {
  info: 'bg-[var(--color-primary)]/10 text-[var(--color-primary)] border-[var(--color-primary)]/30',
  success: 'bg-green-500/10 text-green-500 border-green-500/30',
  warning: 'bg-yellow-500/10 text-yellow-500 border-yellow-500/30',
  error: 'bg-red-500/10 text-red-500 border-red-500/30',
};

// ---------------------------------------------------------------------------
// Individual toast entry
// ---------------------------------------------------------------------------

interface ToastEntryProps {
  toast: ToastItem;
  onDismiss: (id: string) => void;
}

function ToastEntry({ toast, onDismiss }: ToastEntryProps) {
  const [isExiting, setIsExiting] = useState(false);

  const handleDismiss = useCallback(() => {
    setIsExiting(true);
    // Allow exit animation to complete before removing from state
    setTimeout(() => onDismiss(toast.id), 200);
  }, [onDismiss, toast.id]);

  return (
    <div
      className={clsx(
        'flex items-center gap-2 px-4 py-3 rounded-lg border shadow-lg max-w-sm',
        'transition-all duration-200',
        COLOR_MAP[toast.severity],
        isExiting
          ? 'animate-out fade-out slide-out-to-right-2'
          : 'animate-in fade-in slide-in-from-right-2',
      )}
      role="alert"
      aria-live="polite"
    >
      {/* Severity icon */}
      <span className="material-symbols-outlined text-lg shrink-0">
        {ICON_MAP[toast.severity]}
      </span>

      {/* Message */}
      <span className="text-sm font-medium flex-1">{toast.message}</span>

      {/* Optional action button */}
      {toast.action && (
        <button
          onClick={toast.action.onClick}
          className="text-xs font-semibold underline underline-offset-2 hover:opacity-80 transition-opacity shrink-0"
        >
          {toast.action.label}
        </button>
      )}

      {/* Dismiss button */}
      <button
        onClick={handleDismiss}
        className="ml-1 p-1 rounded hover:bg-black/10 transition-colors shrink-0"
        aria-label="Dismiss notification"
      >
        <span className="material-symbols-outlined text-sm">close</span>
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Toast stack container
// ---------------------------------------------------------------------------

export function ToastStack() {
  const { toasts, removeToast } = useToast();

  if (toasts.length === 0) return null;

  return (
    <div
      className="fixed top-4 right-4 z-50 flex flex-col gap-2"
      aria-label="Notifications"
    >
      {toasts.map((toast) => (
        <ToastEntry key={toast.id} toast={toast} onDismiss={removeToast} />
      ))}
    </div>
  );
}

export default ToastStack;
