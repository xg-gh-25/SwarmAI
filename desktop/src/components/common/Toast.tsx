/** @deprecated Use useToast() from contexts/ToastContext instead. This component will be removed in a future cleanup. */
import { useEffect, useState } from 'react';
import clsx from 'clsx';

export type ToastType = 'info' | 'success' | 'warning' | 'error';

interface ToastProps {
  /** Message to display */
  message: string;
  /** Type of toast - affects styling */
  type?: ToastType;
  /** Duration in ms before auto-dismiss (0 = no auto-dismiss) */
  duration?: number;
  /** Callback when toast is dismissed */
  onDismiss: () => void;
}

/**
 * Toast - Simple notification component
 * 
 * Displays a brief message that auto-dismisses after a duration.
 * Used for non-critical notifications like scope changes.
 */
export function Toast({ message, type = 'info', duration = 3000, onDismiss }: ToastProps) {
  const [isVisible, setIsVisible] = useState(true);

  useEffect(() => {
    if (duration > 0) {
      const timer = setTimeout(() => {
        setIsVisible(false);
        // Allow animation to complete before calling onDismiss
        setTimeout(onDismiss, 200);
      }, duration);
      return () => clearTimeout(timer);
    }
  }, [duration, onDismiss]);

  const iconMap: Record<ToastType, string> = {
    info: 'info',
    success: 'check_circle',
    warning: 'warning',
    error: 'error',
  };

  const colorMap: Record<ToastType, string> = {
    info: 'bg-[var(--color-primary)]/10 text-[var(--color-primary)] border-[var(--color-primary)]/30',
    success: 'bg-green-500/10 text-green-500 border-green-500/30',
    warning: 'bg-yellow-500/10 text-yellow-500 border-yellow-500/30',
    error: 'bg-red-500/10 text-red-500 border-red-500/30',
  };

  return (
    <div
      className={clsx(
        'fixed bottom-4 right-4 z-50 flex items-center gap-2 px-4 py-3 rounded-lg border shadow-lg',
        'transition-all duration-200',
        colorMap[type],
        isVisible ? 'animate-in slide-in-from-bottom-2 fade-in' : 'animate-out slide-out-to-bottom-2 fade-out'
      )}
      role="alert"
      aria-live="polite"
    >
      <span className="material-symbols-outlined text-lg">{iconMap[type]}</span>
      <span className="text-sm font-medium">{message}</span>
      <button
        onClick={() => {
          setIsVisible(false);
          setTimeout(onDismiss, 200);
        }}
        className="ml-2 p-1 rounded hover:bg-black/10 transition-colors"
        aria-label="Dismiss notification"
      >
        <span className="material-symbols-outlined text-sm">close</span>
      </button>
    </div>
  );
}

export default Toast;
