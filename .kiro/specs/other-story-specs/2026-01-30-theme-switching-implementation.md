# Theme Switching Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add light/dark/system theme switching to the Settings page while preserving the existing dark theme unchanged.

**Architecture:** Use CSS variables with `:root` (light) and `:root.dark` (dark) selectors. ThemeContext manages state with localStorage persistence. FOUC prevention script in index.html applies theme class before React hydration.

**Tech Stack:** React Context API, CSS Custom Properties, localStorage, matchMedia API for system preference detection.

---

## Task 1: Setup CSS Variables in index.css

**Files:**
- Modify: `desktop/src/index.css`

**Step 1: Add theme CSS variables**

Replace the current `@theme` block and body styles with CSS variable-based theming:

```css
@import "tailwindcss";

/* Theme CSS Variables */
:root {
  /* Light theme (default) */
  --font-family-sans: 'Space Grotesk', system-ui, sans-serif;

  /* Background colors */
  --color-bg: #f8fafc;
  --color-card: #ffffff;
  --color-hover: #f1f5f9;
  --color-border: #e2e8f0;

  /* Primary colors (unchanged) */
  --color-primary: #2b6cee;
  --color-primary-hover: #1d5cd6;
  --color-primary-light: #3d7ef0;

  /* Text colors */
  --color-text: #1e293b;
  --color-text-muted: #64748b;
  --color-placeholder: #94a3b8;

  /* Status colors */
  --color-status-success: #16a34a;
  --color-status-error: #dc2626;
  --color-status-warning: #d97706;
  --color-status-online: #16a34a;
  --color-status-offline: #6b7280;

  /* Input colors */
  --color-input-bg: #ffffff;
  --color-input-border: #cbd5e1;

  /* Sidebar colors */
  --color-sidebar-icon: #64748b;
  --color-sidebar-icon-active: #2b6cee;

  /* Code block (keep dark for readability) */
  --color-code-bg: #1e293b;
}

:root.dark {
  /* Dark theme */
  /* Background colors */
  --color-bg: #101622;
  --color-card: #1a1f2e;
  --color-hover: #252b3d;
  --color-border: #2d3548;

  /* Text colors */
  --color-text: #ffffff;
  --color-text-muted: #9da6b9;
  --color-placeholder: #6b7280;

  /* Status colors */
  --color-status-success: #22c55e;
  --color-status-error: #ef4444;
  --color-status-warning: #f59e0b;
  --color-status-online: #22c55e;
  --color-status-offline: #6b7280;

  /* Input colors */
  --color-input-bg: #1a1f2e;
  --color-input-border: #2d3548;

  /* Sidebar colors */
  --color-sidebar-icon: #9da6b9;
  --color-sidebar-icon-active: #ffffff;

  /* Code block */
  --color-code-bg: #101622;
}

/* Tailwind theme integration */
@theme {
  --font-family-sans: var(--font-family-sans);

  --color-primary: var(--color-primary);
  --color-primary-hover: var(--color-primary-hover);
  --color-primary-light: var(--color-primary-light);

  --color-dark-bg: var(--color-bg);
  --color-dark-card: var(--color-card);
  --color-dark-hover: var(--color-hover);
  --color-dark-border: var(--color-border);

  --color-muted: var(--color-text-muted);

  --color-status-online: var(--color-status-online);
  --color-status-offline: var(--color-status-offline);
  --color-status-error: var(--color-status-error);
  --color-status-warning: var(--color-status-warning);
  --color-status-success: var(--color-status-success);
}

/* Base styles */
html {
  font-size: 14px;
  font-family: var(--font-family-sans);
}

body {
  background-color: var(--color-bg);
  color: var(--color-text);
}

/* Scrollbar styling */
::-webkit-scrollbar {
  width: 8px;
  height: 8px;
}

::-webkit-scrollbar-track {
  background: var(--color-card);
}

::-webkit-scrollbar-thumb {
  background: var(--color-border);
  border-radius: 4px;
}

::-webkit-scrollbar-thumb:hover {
  background: var(--color-text-muted);
}
```

**Step 2: Verify CSS compiles**

Run: `cd /home/ubuntu/workspace/owork/.worktrees/feature-theme-switching/desktop && npm run build 2>&1 | head -20`
Expected: Build succeeds or shows non-CSS errors

**Step 3: Commit**

```bash
git add desktop/src/index.css
git commit -m "feat(theme): add CSS variables for light/dark themes"
```

---

## Task 2: Create ThemeContext

**Files:**
- Create: `desktop/src/contexts/ThemeContext.tsx`

**Step 1: Create the ThemeContext file**

```typescript
import { createContext, useContext, useEffect, useState, ReactNode } from 'react';

type Theme = 'light' | 'dark' | 'system';
type ResolvedTheme = 'light' | 'dark';

interface ThemeContextType {
  theme: Theme;
  resolvedTheme: ResolvedTheme;
  setTheme: (theme: Theme) => void;
}

const ThemeContext = createContext<ThemeContextType | undefined>(undefined);

function getSystemTheme(): ResolvedTheme {
  if (typeof window === 'undefined') return 'dark';
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function getStoredTheme(): Theme {
  if (typeof window === 'undefined') return 'system';
  const stored = localStorage.getItem('theme');
  if (stored === 'light' || stored === 'dark' || stored === 'system') {
    return stored;
  }
  return 'system';
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

  // Apply theme on mount and when theme changes
  useEffect(() => {
    const resolved = resolveTheme(theme);
    setResolvedTheme(resolved);
    applyTheme(resolved);
    localStorage.setItem('theme', theme);
  }, [theme]);

  // Listen for system theme changes
  useEffect(() => {
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

  return (
    <ThemeContext.Provider value={{ theme, resolvedTheme, setTheme }}>
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
```

**Step 2: Commit**

```bash
git add desktop/src/contexts/ThemeContext.tsx
git commit -m "feat(theme): add ThemeContext with system preference support"
```

---

## Task 3: Add FOUC Prevention Script

**Files:**
- Modify: `desktop/index.html`

**Step 1: Add inline script to prevent FOUC**

Replace the current index.html with:

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <link rel="icon" type="image/svg+xml" href="/vite.svg" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Claude Agent Platform</title>
    <!-- FOUC Prevention: Apply theme before CSS loads -->
    <script>
      (function() {
        try {
          var theme = localStorage.getItem('theme');
          var systemDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
          var resolved = (theme === 'light' || theme === 'dark') ? theme : (systemDark ? 'dark' : 'light');
          document.documentElement.classList.add(resolved);
        } catch (e) {
          document.documentElement.classList.add('dark');
        }
      })();
    </script>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@20..48,100..700,0..1,-50..200" rel="stylesheet" />
  </head>
  <body class="font-sans antialiased">
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

**Step 2: Commit**

```bash
git add desktop/index.html
git commit -m "feat(theme): add FOUC prevention script"
```

---

## Task 4: Wrap App with ThemeProvider

**Files:**
- Modify: `desktop/src/App.tsx`

**Step 1: Import and wrap with ThemeProvider**

```typescript
import { useEffect } from 'react';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ThemeProvider } from './contexts/ThemeContext';
import { Layout, BackendStartupOverlay, UpdateNotification } from './components/common';
import ChatPage from './pages/ChatPage';
import AgentsPage from './pages/AgentsPage';
import SkillsPage from './pages/SkillsPage';
import MCPPage from './pages/MCPPage';
import PluginsPage from './pages/PluginsPage';
import DashboardPage from './pages/DashboardPage';
import SettingsPage from './pages/SettingsPage';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 1000 * 60 * 5, // 5 minutes
      retry: 1,
    },
  },
});

// Check if running in development mode
const isDev = import.meta.env.DEV;

export default function App() {
  // Log mode on startup
  useEffect(() => {
    if (isDev) {
      console.log('Development mode: using manual backend on port 8000');
    }
    // In production mode, BackendStartupOverlay handles backend initialization
  }, []);

  return (
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        {/* Backend startup overlay - only shown in production mode */}
        {!isDev && <BackendStartupOverlay />}
        {/* Update notification - only shown in production mode */}
        {!isDev && <UpdateNotification />}
        <BrowserRouter>
          <Routes>
            <Route path="/" element={<Layout />}>
              <Route index element={<DashboardPage />} />
              <Route path="chat" element={<ChatPage />} />
              <Route path="agents" element={<AgentsPage />} />
              <Route path="skills" element={<SkillsPage />} />
              <Route path="mcp" element={<MCPPage />} />
              <Route path="plugins" element={<PluginsPage />} />
              <Route path="settings" element={<SettingsPage />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </QueryClientProvider>
    </ThemeProvider>
  );
}
```

**Step 2: Commit**

```bash
git add desktop/src/App.tsx
git commit -m "feat(theme): wrap App with ThemeProvider"
```

---

## Task 5: Add i18n Strings

**Files:**
- Modify: `desktop/src/i18n/locales/zh.json`
- Modify: `desktop/src/i18n/locales/en.json`

**Step 1: Add theme translations to zh.json**

Add after `"language"` section in `settings`:

```json
    "theme": {
      "title": "主题 / Theme",
      "description": "选择界面显示主题",
      "light": "浅色",
      "dark": "深色",
      "system": "跟随系统"
    },
```

**Step 2: Add theme translations to en.json**

Add after `"language"` section in `settings`:

```json
    "theme": {
      "title": "Theme / 主题",
      "description": "Select display theme",
      "light": "Light",
      "dark": "Dark",
      "system": "System"
    },
```

**Step 3: Commit**

```bash
git add desktop/src/i18n/locales/zh.json desktop/src/i18n/locales/en.json
git commit -m "feat(theme): add i18n strings for theme settings"
```

---

## Task 6: Add Theme Selector to Settings Page

**Files:**
- Modify: `desktop/src/pages/SettingsPage.tsx`

**Step 1: Import useTheme**

Add at top of imports:

```typescript
import { useTheme } from '../contexts/ThemeContext';
```

**Step 2: Add theme state in component**

After `const { t, i18n } = useTranslation();`:

```typescript
const { theme, setTheme } = useTheme();
```

**Step 3: Add Theme Settings section**

Add after the Language Settings section (after line ~353):

```tsx
      {/* Theme Settings */}
      <section className="mb-8 bg-[var(--color-card)] rounded-lg p-6">
        <h2 className="text-lg font-semibold text-[var(--color-text)] mb-2">{t('settings.theme.title')}</h2>
        <p className="text-sm text-[var(--color-text-muted)] mb-4">{t('settings.theme.description')}</p>
        <div className="flex gap-3">
          <button
            onClick={() => setTheme('light')}
            className={`flex-1 px-4 py-3 rounded-lg text-sm font-medium transition-colors flex items-center justify-center gap-2 ${
              theme === 'light'
                ? 'bg-[var(--color-primary)] text-white'
                : 'bg-[var(--color-bg)] text-[var(--color-text-muted)] border border-[var(--color-border)] hover:border-[var(--color-text-muted)]'
            }`}
          >
            {theme === 'light' && <span className="material-symbols-outlined text-sm">check</span>}
            <span className="material-symbols-outlined text-sm">light_mode</span>
            {t('settings.theme.light')}
          </button>
          <button
            onClick={() => setTheme('dark')}
            className={`flex-1 px-4 py-3 rounded-lg text-sm font-medium transition-colors flex items-center justify-center gap-2 ${
              theme === 'dark'
                ? 'bg-[var(--color-primary)] text-white'
                : 'bg-[var(--color-bg)] text-[var(--color-text-muted)] border border-[var(--color-border)] hover:border-[var(--color-text-muted)]'
            }`}
          >
            {theme === 'dark' && <span className="material-symbols-outlined text-sm">check</span>}
            <span className="material-symbols-outlined text-sm">dark_mode</span>
            {t('settings.theme.dark')}
          </button>
          <button
            onClick={() => setTheme('system')}
            className={`flex-1 px-4 py-3 rounded-lg text-sm font-medium transition-colors flex items-center justify-center gap-2 ${
              theme === 'system'
                ? 'bg-[var(--color-primary)] text-white'
                : 'bg-[var(--color-bg)] text-[var(--color-text-muted)] border border-[var(--color-border)] hover:border-[var(--color-text-muted)]'
            }`}
          >
            {theme === 'system' && <span className="material-symbols-outlined text-sm">check</span>}
            <span className="material-symbols-outlined text-sm">contrast</span>
            {t('settings.theme.system')}
          </button>
        </div>
      </section>
```

**Step 4: Update existing hardcoded colors to CSS variables**

Replace hardcoded hex colors in SettingsPage.tsx with CSS variables:
- `bg-[#1a1f2e]` → `bg-[var(--color-card)]`
- `bg-[#101622]` → `bg-[var(--color-bg)]`
- `bg-[#2b6cee]` → `bg-[var(--color-primary)]`
- `text-white` → `text-[var(--color-text)]`
- `text-gray-400` → `text-[var(--color-text-muted)]`
- `text-gray-500` → `text-[var(--color-text-muted)]`
- `border-gray-700` → `border-[var(--color-border)]`

**Step 5: Commit**

```bash
git add desktop/src/pages/SettingsPage.tsx
git commit -m "feat(theme): add theme selector to Settings page"
```

---

## Task 7: Update MarkdownRenderer for Dynamic Theme

**Files:**
- Modify: `desktop/src/components/common/MarkdownRenderer.tsx`

**Step 1: Import useTheme and update mermaid initialization**

Replace the static mermaid.initialize with dynamic configuration:

```typescript
import { useTheme } from '../../contexts/ThemeContext';

// Remove the top-level mermaid.initialize call

// Inside MermaidDiagram component, add:
const { resolvedTheme } = useTheme();

// Update useEffect to reinitialize mermaid when theme changes:
useEffect(() => {
  mermaid.initialize({
    startOnLoad: false,
    theme: resolvedTheme === 'dark' ? 'dark' : 'default',
    themeVariables: resolvedTheme === 'dark' ? {
      primaryColor: '#2b6cee',
      primaryTextColor: '#ffffff',
      primaryBorderColor: '#3d4f6f',
      lineColor: '#9da6b9',
      secondaryColor: '#1a1f2e',
      tertiaryColor: '#101622',
      background: '#1a1f2e',
      mainBkg: '#1a1f2e',
      nodeBorder: '#3d4f6f',
      clusterBkg: '#101622',
      titleColor: '#ffffff',
      edgeLabelBackground: '#1a1f2e',
    } : {
      primaryColor: '#2b6cee',
      primaryTextColor: '#1e293b',
      primaryBorderColor: '#cbd5e1',
      lineColor: '#64748b',
      secondaryColor: '#f1f5f9',
      tertiaryColor: '#f8fafc',
      background: '#ffffff',
      mainBkg: '#ffffff',
      nodeBorder: '#cbd5e1',
      clusterBkg: '#f8fafc',
      titleColor: '#1e293b',
      edgeLabelBackground: '#ffffff',
    },
    fontFamily: 'Space Grotesk, sans-serif',
  });
}, [resolvedTheme]);
```

**Step 2: Update PNG download background color**

Change the hardcoded `#1a1f2e` to use resolvedTheme:

```typescript
ctx.fillStyle = resolvedTheme === 'dark' ? '#1a1f2e' : '#ffffff';
```

**Step 3: Commit**

```bash
git add desktop/src/components/common/MarkdownRenderer.tsx
git commit -m "feat(theme): make mermaid diagrams theme-aware"
```

---

## Task 8: Update Layout Component

**Files:**
- Modify: `desktop/src/components/common/Layout.tsx`

**Step 1: Check and update hardcoded colors**

Read the file and replace any hardcoded hex colors with CSS variables:
- `#101622` → `var(--color-bg)`
- `#1a1f2e` → `var(--color-card)`
- `#2d3548` → `var(--color-border)`
- `#9da6b9` → `var(--color-text-muted)`

**Step 2: Commit**

```bash
git add desktop/src/components/common/Layout.tsx
git commit -m "feat(theme): update Layout colors to use CSS variables"
```

---

## Task 9: Test and Verify

**Step 1: Build the app**

Run: `cd /home/ubuntu/workspace/owork/.worktrees/feature-theme-switching/desktop && npm run build`
Expected: Build succeeds

**Step 2: Run lint**

Run: `npm run lint`
Expected: No new errors (existing errors are acceptable)

**Step 3: Manual testing checklist**

- [ ] Settings page shows theme selector (Light/Dark/System buttons)
- [ ] Clicking Light switches to light theme
- [ ] Clicking Dark switches to dark theme
- [ ] Clicking System follows OS preference
- [ ] Theme persists after page refresh
- [ ] No FOUC (flash of unstyled content) on page load
- [ ] Mermaid diagrams render correctly in both themes

**Step 4: Final commit (if any fixes needed)**

```bash
git add -A
git commit -m "fix(theme): address review feedback"
```

---

## Summary

| Task | Description | Files |
|------|-------------|-------|
| 1 | Setup CSS Variables | index.css |
| 2 | Create ThemeContext | contexts/ThemeContext.tsx |
| 3 | Add FOUC Prevention | index.html |
| 4 | Wrap App with ThemeProvider | App.tsx |
| 5 | Add i18n Strings | locales/zh.json, en.json |
| 6 | Add Theme Selector UI | SettingsPage.tsx |
| 7 | Dynamic Mermaid Theme | MarkdownRenderer.tsx |
| 8 | Update Layout Colors | Layout.tsx |
| 9 | Test and Verify | - |
