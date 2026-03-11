/**
 * Health monitoring context and provider.
 *
 * Wraps the {@link useHealthMonitor} hook in a React context so any
 * component in the tree can read the current backend health state and
 * trigger an on-demand health check without prop drilling.
 *
 * Key exports:
 * - `HealthProvider`  — context provider; place inside `ToastProvider`
 * - `useHealth`       — hook returning `{ health, triggerHealthCheck }`
 *
 * Validates: Requirements 1.6, 1.7
 */

import { createContext, useContext, useMemo, type ReactNode } from 'react';
import type { HealthState } from '../types';
import { useHealthMonitor } from '../hooks/useHealthMonitor';

// ---------------------------------------------------------------------------
// Context value interface
// ---------------------------------------------------------------------------

export interface HealthContextValue {
  /** Current backend health state. */
  health: HealthState;
  /** Trigger an immediate out-of-cycle health check (e.g. on SERVICE_UNAVAILABLE). */
  triggerHealthCheck: () => void;
}

const HealthContext = createContext<HealthContextValue | undefined>(undefined);

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

interface HealthProviderProps {
  children: ReactNode;
}

/**
 * Provides backend health state to the component tree.
 *
 * Must be rendered inside `ToastProvider` (useHealthMonitor depends on
 * useToast for transition notifications).
 */
export function HealthProvider({ children }: HealthProviderProps) {
  const { state, checkNow } = useHealthMonitor();

  const value = useMemo<HealthContextValue>(
    () => ({ health: state, triggerHealthCheck: checkNow }),
    [state, checkNow],
  );

  return (
    <HealthContext.Provider value={value}>
      {children}
    </HealthContext.Provider>
  );
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

/**
 * Consume the health context. Must be called within a `HealthProvider`.
 */
export function useHealth(): HealthContextValue {
  const context = useContext(HealthContext);
  if (context === undefined) {
    throw new Error('useHealth must be used within a HealthProvider');
  }
  return context;
}
