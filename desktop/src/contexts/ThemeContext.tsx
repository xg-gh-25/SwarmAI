/* eslint-disable react-refresh/only-export-components */
import { createContext, useContext, useEffect, useState, ReactNode } from 'react';

type Theme = 'light' | 'dark' | 'system';
type ResolvedTheme = 'light' | 'dark';
export type AccentColor = 'blue' | 'purple' | 'green' | 'orange' | 'rose';

export const ACCENT_PRESETS: { id: AccentColor; label: string; color: string }[] = [
  { id: 'blue',   label: 'Blue',   color: '#2b6cee' },
  { id: 'purple', label: 'Purple', color: '#8b5cf6' },
  { id: 'green',  label: 'Green',  color: '#10b981' },
  { id: 'orange', label: 'Orange', color: '#f59e0b' },
  { id: 'rose',   label: 'Rose',   color: '#f43f5e' },
];

interface ThemeContextType {
  theme: Theme;
  resolvedTheme: ResolvedTheme;
  accentColor: AccentColor;
  setTheme: (theme: Theme) => void;
  setAccentColor: (accent: AccentColor) => void;
}

const ThemeContext = createContext<ThemeContextType | undefined>(undefined);

function getSystemTheme(): ResolvedTheme {
  // Guard BOTH "no window" (SSR/JSDOM) AND "window present but matchMedia absent"
  // (some embedded WebViews / non-standard hosts). This runs in ThemeProvider's
  // render-time state initializer, and ThemeProvider is ABOVE the app-level
  // ErrorBoundary — so an unguarded throw here is a raw white screen with no
  // Reload, not a catchable error. Fall back to the app's default theme.
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return 'dark';
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function getStoredTheme(): Theme {
  if (typeof window === 'undefined') return 'dark';
  const stored = localStorage.getItem('theme');
  if (stored === 'light' || stored === 'dark' || stored === 'system') {
    return stored;
  }
  return 'dark';
}

function getStoredAccent(): AccentColor {
  if (typeof window === 'undefined') return 'blue';
  const stored = localStorage.getItem('accentColor');
  if (stored && ACCENT_PRESETS.some(p => p.id === stored)) return stored as AccentColor;
  return 'blue';
}

function applyAccent(accent: AccentColor) {
  const root = document.documentElement;
  if (accent === 'blue') {
    delete root.dataset.accent;
  } else {
    root.dataset.accent = accent;
  }
}

function resolveTheme(theme: Theme): ResolvedTheme {
  if (theme === 'system') {
    return getSystemTheme();
  }
  return theme;
}

function applyTheme(resolvedTheme: ResolvedTheme) {
  const root = document.documentElement;
  root.classList.remove('light', 'dark');
  root.classList.add(resolvedTheme);
}

interface ThemeProviderProps {
  children: ReactNode;
}

export function ThemeProvider({ children }: ThemeProviderProps) {
  const [theme, setThemeState] = useState<Theme>(getStoredTheme);
  const [resolvedTheme, setResolvedTheme] = useState<ResolvedTheme>(() => resolveTheme(getStoredTheme()));
  const [accentColor, setAccentState] = useState<AccentColor>(getStoredAccent);

  // Remove .no-transitions after first React paint to enable smooth toggle
  useEffect(() => {
    // Use rAF to ensure styles have been applied, then enable transitions
    requestAnimationFrame(() => {
      document.documentElement.classList.remove('no-transitions');
    });
  }, []);

  useEffect(() => {
    const resolved = resolveTheme(theme);
    setResolvedTheme(resolved);
    applyTheme(resolved);
    localStorage.setItem('theme', theme);
  }, [theme]);

  useEffect(() => {
    applyAccent(accentColor);
    localStorage.setItem('accentColor', accentColor);
  }, [accentColor]);

  useEffect(() => {
    // Host may lack matchMedia (see getSystemTheme) — skip the system-theme
    // listener rather than throw. Theme still works (defaults + explicit choice).
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return;
    const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');

    const handleChange = () => {
      if (theme === 'system') {
        const resolved = getSystemTheme();
        setResolvedTheme(resolved);
        applyTheme(resolved);
      }
    };

    mediaQuery.addEventListener('change', handleChange);
    return () => mediaQuery.removeEventListener('change', handleChange);
  }, [theme]);

  const setTheme = (newTheme: Theme) => {
    setThemeState(newTheme);
  };

  const setAccentColor = (accent: AccentColor) => {
    setAccentState(accent);
  };

  return (
    <ThemeContext.Provider value={{ theme, resolvedTheme, accentColor, setTheme, setAccentColor }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme() {
  const context = useContext(ThemeContext);
  if (context === undefined) {
    throw new Error('useTheme must be used within a ThemeProvider');
  }
  return context;
}
