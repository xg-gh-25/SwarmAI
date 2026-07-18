/**
 * A6 (startup hazard): ThemeProvider must not white-screen the app on a host
 * lacking window.matchMedia.
 *
 * ThemeProvider is the OUTERMOST provider, ABOVE the app-level ErrorBoundary
 * (App.tsx: ThemeProvider > … > ErrorBoundary variant="app"). Its state
 * initializer runs getSystemTheme() → window.matchMedia(...) at first render.
 * On a WebView/embedded host where `window` exists but `matchMedia` doesn't,
 * that throws synchronously during ThemeProvider render → React unmounts the
 * whole tree → RAW WHITE SCREEN, no Reload button (nothing above can catch it).
 * getSystemTheme already guards `typeof window === 'undefined'` but NOT the
 * window-present-but-matchMedia-absent case.
 */
import { render } from '@testing-library/react';
import { describe, it, expect, afterEach } from 'vitest';
import { ThemeProvider, useTheme } from './ThemeContext';

function Probe() {
  const { resolvedTheme } = useTheme();
  return <div data-testid="probe">{resolvedTheme}</div>;
}

describe('A6: ThemeProvider survives a host without window.matchMedia', () => {
  const original = window.matchMedia;
  afterEach(() => {
    // restore
    Object.defineProperty(window, 'matchMedia', { value: original, configurable: true, writable: true });
  });

  it('does not throw when window.matchMedia is undefined (no raw white screen)', () => {
    // Simulate a WebView/embedded host: window present, matchMedia absent.
    // @ts-expect-error deliberately removing the API for the test
    delete window.matchMedia;

    expect(() => render(<ThemeProvider><Probe /></ThemeProvider>)).not.toThrow();
  });
});
