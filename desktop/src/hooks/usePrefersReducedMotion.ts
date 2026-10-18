/**
 * usePrefersReducedMotion — live `(prefers-reduced-motion: reduce)` state.
 *
 * Returns true when the OS asks for reduced motion and follows changes while
 * mounted. Returns false where `window.matchMedia` is unavailable (jsdom, old
 * webviews) instead of throwing, so components that use it render everywhere.
 *
 * @exports usePrefersReducedMotion
 */
import { useEffect, useState } from 'react';

const QUERY = '(prefers-reduced-motion: reduce)';

function query(): MediaQueryList | null {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return null;
  return window.matchMedia(QUERY);
}

export function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState<boolean>(() => query()?.matches ?? false);

  useEffect(() => {
    const mql = query();
    if (!mql) return;
    const onChange = () => setReduced(mql.matches);
    onChange();
    if (typeof mql.addEventListener === 'function') {
      mql.addEventListener('change', onChange);
      return () => mql.removeEventListener('change', onChange);
    }
    // Safari < 14 only exposes the deprecated listener API.
    mql.addListener(onChange);
    return () => mql.removeListener(onChange);
  }, []);

  return reduced;
}
