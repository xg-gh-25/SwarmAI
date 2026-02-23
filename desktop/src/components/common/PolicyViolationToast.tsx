import { useState } from 'react';
import clsx from 'clsx';
import type { PolicyViolationDetail } from '../../types';

interface PolicyViolationToastProps {
  /** Human-readable error message */
  message: string;
  /** List of policy violations */
  violations: PolicyViolationDetail[];
  /** Callback when "Resolve" is clicked — navigate to workspace settings */
  onResolve: () => void;
  /** Callback when toast is dismissed */
  onDismiss: () => void;
}

/**
 * PolicyViolationToast — notification shown when task execution is blocked
 * by workspace policy (409 Conflict). Offers a "Resolve" action to navigate
 * to workspace settings where the user can enable the required capabilities.
 *
 * Requirements: 34.4, 34.5
 */
export function PolicyViolationToast({
  message,
  violations,
  onResolve,
  onDismiss,
}: PolicyViolationToastProps) {
  const [isVisible, setIsVisible] = useState(true);

  const handleDismiss = () => {
    setIsVisible(false);
    setTimeout(onDismiss, 200);
  };

  return (
    <div
      className={clsx(
        'fixed bottom-4 right-4 z-50 max-w-md rounded-lg border shadow-lg',
        'bg-yellow-500/10 text-yellow-500 border-yellow-500/30',
        'transition-all duration-200',
        isVisible
          ? 'animate-in slide-in-from-bottom-2 fade-in'
          : 'animate-out slide-out-to-bottom-2 fade-out'
      )}
      role="alert"
      aria-live="assertive"
    >
      <div className="px-4 py-3">
        <div className="flex items-start gap-2">
          <span className="material-symbols-outlined text-lg mt-0.5">policy</span>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium">{message}</p>
            {violations.length > 0 && (
              <ul className="mt-1 text-xs opacity-80 list-disc list-inside">
                {violations.map((v, i) => (
                  <li key={i}>{v.message}</li>
                ))}
              </ul>
            )}
          </div>
          <button
            onClick={handleDismiss}
            className="p-1 rounded hover:bg-black/10 transition-colors flex-shrink-0"
            aria-label="Dismiss notification"
          >
            <span className="material-symbols-outlined text-sm">close</span>
          </button>
        </div>
        <div className="mt-2 flex justify-end gap-2">
          <button
            onClick={handleDismiss}
            className="px-3 py-1 text-xs rounded hover:bg-black/10 transition-colors"
          >
            Dismiss
          </button>
          <button
            onClick={() => {
              onResolve();
              handleDismiss();
            }}
            className="px-3 py-1 text-xs rounded bg-yellow-500/20 hover:bg-yellow-500/30 font-medium transition-colors"
          >
            Resolve in Settings
          </button>
        </div>
      </div>
    </div>
  );
}

export default PolicyViolationToast;
