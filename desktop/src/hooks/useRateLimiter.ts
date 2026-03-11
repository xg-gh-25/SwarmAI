/**
 * Rate limiter hook for enforcing HTTP 429 Retry-After cooldowns.
 *
 * Tracks per-endpoint rate limits in a ref-based Map to avoid unnecessary
 * re-renders. Exposes helpers for checking/registering limits and fires
 * toast notifications on activation and expiry.
 *
 * Key exports:
 * - `useRateLimiter` — hook returning rate-limit helpers
 *
 * Validates: Requirements 6.1, 6.2, 6.4
 */

import { useRef, useState, useCallback, useEffect } from 'react';
import type { RateLimitEntry } from '../types';
import { useToast } from '../contexts/ToastContext';
import { setRateLimitCallback } from '../services/api';

export interface UseRateLimiterReturn {
  /** Register a new rate limit for an endpoint. */
  registerRateLimit: (endpoint: string, retryAfterSec: number) => void;
  /** Check whether an endpoint is currently rate-limited. */
  isLimited: (endpoint: string) => boolean;
  /** Seconds remaining on a rate limit (0 if not limited). */
  getRemainingSeconds: (endpoint: string) => number;
  /** Snapshot of all active limits (for display). */
  activeLimits: RateLimitEntry[];
}

export function useRateLimiter(): UseRateLimiterReturn {
  const limitsRef = useRef<Map<string, RateLimitEntry>>(new Map());
  const { addToast } = useToast();

  // Display-mirror state — updated by a 1-second sweep so consuming
  // components can read `activeLimits` without polling the ref.
  const [activeLimits, setActiveLimits] = useState<RateLimitEntry[]>([]);

  // Periodic sweep: prune expired entries and sync display state.
  useEffect(() => {
    const interval = setInterval(() => {
      const now = Date.now();
      let changed = false;
      for (const [ep, entry] of limitsRef.current) {
        if (entry.expiresAt <= now) {
          limitsRef.current.delete(ep);
          changed = true;
          addToast({
            severity: 'info',
            message: `Rate limit expired for ${ep}. You may resume.`,
            autoDismiss: true,
            id: `rate-limit-expired-${ep}`,
          });
        }
      }
      if (changed || limitsRef.current.size > 0) {
        setActiveLimits(Array.from(limitsRef.current.values()));
      }
    }, 1000);
    return () => clearInterval(interval);
  }, [addToast]);

  const registerRateLimit = useCallback(
    (endpoint: string, retryAfterSec: number) => {
      const entry: RateLimitEntry = {
        endpoint,
        expiresAt: Date.now() + retryAfterSec * 1000,
        retryAfterSec,
      };
      limitsRef.current.set(endpoint, entry);
      setActiveLimits(Array.from(limitsRef.current.values()));

      addToast({
        severity: 'warning',
        message: `Rate limited on ${endpoint}. Please wait ${retryAfterSec}s.`,
        autoDismiss: false,
        id: `rate-limit-${endpoint}`,
      });
    },
    [addToast],
  );

  // Register the rate-limit callback so the axios interceptor can notify
  // this hook when a 429 is received. Unregister on unmount.
  useEffect(() => {
    setRateLimitCallback(registerRateLimit);
    return () => setRateLimitCallback(null);
  }, [registerRateLimit]);

  const isLimited = useCallback((endpoint: string): boolean => {
    const entry = limitsRef.current.get(endpoint);
    if (!entry) return false;
    if (entry.expiresAt <= Date.now()) {
      limitsRef.current.delete(endpoint);
      return false;
    }
    return true;
  }, []);

  const getRemainingSeconds = useCallback((endpoint: string): number => {
    const entry = limitsRef.current.get(endpoint);
    if (!entry) return 0;
    const remaining = Math.ceil((entry.expiresAt - Date.now()) / 1000);
    return remaining > 0 ? remaining : 0;
  }, []);

  return { registerRateLimit, isLimited, getRemainingSeconds, activeLimits };
}
