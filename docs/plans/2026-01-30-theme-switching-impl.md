# Theme Switching Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add light theme support with three modes (Light, Dark, System) selectable from Settings page.

**Architecture:** Use CSS custom properties for theme colors. Tailwind's `darkMode: 'class'` already configured. ThemeContext manages state, persists to localStorage, and handles system preference detection.

**Tech Stack:** React Context, CSS Variables, Tailwind CSS 4.x, localStorage, `prefers-color-scheme` media query

---

## Task 1: Update CSS Variables in index.css

**Files:**
- Modify: `desktop/src/index.css`

**Step 1: Replace @theme color definitions with semantic CSS variables**

Replace the existing `@theme` block and body styles. Change from static dark colors to CSS variables that change based on `.dark` class.

```css
@import "tailwindcss";

@theme {
  --font-family-sans: 'Space Grotesk', system-ui, sans-serif;

  /* Static colors - same in both themes */
  --color-primary: #2b6cee;
  --color-primary-hover: #1d5cd6;
  --color-primary-light: #3d7ef0;

  --color-status-online: #22c55e;
  --color-status-offline: #6b7280;
  --color-status-error: #ef4444;
  --color-status-warning: #f59e0b;
  --color-status-success: #22c55e;
}

/* Light theme (default) */
:root {
  --color-bg: #f5f5f7;
  --color-card: #ffffff;
  --color-hover: #e5e5e7;
  --color-border: #d1d1d6;
  --color-text: #1d1d1f;
  --color-muted: #6e6e73;
  --color-scrollbar-track: #f0f0f0;
  --color-scrollbar-thumb: #c1c1c1;
  --color-scrollbar-thumb-hover: #a1a1a1;
}

/* Dark theme */
.dark {
  --color-bg: #101622;
  --color-card: #1a1f2e;
  --color-hover: #252b3d;
  --color-border: #2d3548;
  --color-text: #ffffff;
  --color-muted: #9da6b9;
  --color-scrollbar-track: #1a1f2e;
  --color-scrollbar-thumb: #2d3548;
  --color-scrollbar-thumb-hover: #9da6b9;
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
  background: var(--color-scrollbar-track);
}

::-webkit-scrollbar-thumb {
  background: var(--color-scrollbar-thumb);
  border-radius: 4px;
}

::-webkit-scrollbar-thumb:hover {
  background: var(--color-scrollbar-thumb-hover);
}
```

**Step 2: Update glass-effect utility**

```css
/* Custom utilities */
.glass-effect {
  background: color-mix(in srgb, var(--color-card) 80%, transparent);
  backdrop-filter: blur(10px);
}
```

**Step 3: Add light theme highlight.js styles**

Add after the existing dark theme `.hljs` styles:

```css
/* Highlight.js Light Theme */
:root .hljs {
  background: transparent !important;
  color: #24292e;
}

:root .hljs-comment,
:root .hljs-quote {
  color: #6a737d;
  font-style: italic;
}

:root .hljs-keyword,
:root .hljs-selector-tag,
:root .hljs-addition {
  color: #d73a49;
}

:root .hljs-number,
:root .hljs-string,
:root .hljs-meta .hljs-meta-string,
:root .hljs-literal,
:root .hljs-doctag,
:root .hljs-regexp {
  color: #032f62;
}

:root .hljs-title,
:root .hljs-section,
:root .hljs-name,
:root .hljs-selector-id,
:root .hljs-selector-class {
  color: #6f42c1;
}

:root .hljs-attribute,
:root .hljs-attr,
:root .hljs-variable,
:root .hljs-template-variable,
:root .hljs-class .hljs-title,
:root .hljs-type {
  color: #005cc5;
}

:root .hljs-symbol,
:root .hljs-bullet,
:root .hljs-subst,
:root .hljs-meta,
:root .hljs-meta .hljs-keyword,
:root .hljs-selector-attr,
:root .hljs-selector-pseudo,
:root .hljs-link {
  color: #e36209;
}

:root .hljs-built_in,
:root .hljs-deletion {
  color: #b31d28;
}

:root .hljs-formula {
  background: #f6f8fa;
}

:root .hljs-title.function_,
:root .hljs-title.class_ {
  color: #6f42c1;
}

:root .hljs-params {
  color: #24292e;
}

:root .hljs-operator {
  color: #d73a49;
}

:root .hljs-property {
  color: #005cc5;
}

:root .hljs-punctuation {
  color: #24292e;
}

/* Dark theme highlight.js - override with .dark selector */
.dark .hljs {
  background: transparent !important;
  color: #e6edf3;
}

.dark .hljs-comment,
.dark .hljs-quote {
  color: #8b949e;
  font-style: italic;
}

.dark .hljs-keyword,
.dark .hljs-selector-tag,
.dark .hljs-addition {
  color: #ff7b72;
}

.dark .hljs-number,
.dark .hljs-string,
.dark .hljs-meta .hljs-meta-string,
.dark .hljs-literal,
.dark .hljs-doctag,
.dark .hljs-regexp {
  color: #a5d6ff;
}

.dark .hljs-title,
.dark .hljs-section,
.dark .hljs-name,
.dark .hljs-selector-id,
.dark .hljs-selector-class {
  color: #d2a8ff;
}

.dark .hljs-attribute,
.dark .hljs-attr,
.dark .hljs-variable,
.dark .hljs-template-variable,
.dark .hljs-class .hljs-title,
.dark .hljs-type {
  color: #79c0ff;
}

.dark .hljs-symbol,
.dark .hljs-bullet,
.dark .hljs-subst,
.dark .hljs-meta,
.dark .hljs-meta .hljs-keyword,
.dark .hljs-selector-attr,
.dark .hljs-selector-pseudo,
.dark .hljs-link {
  color: #ffa657;
}

.dark .hljs-built_in,
.dark .hljs-deletion {
  color: #ffa198;
}

.dark .hljs-formula {
  background: #161b22;
}

.dark .hljs-title.function_,
.dark .hljs-title.class_ {
  color: #d2a8ff;
}

.dark .hljs-params {
  color: #e6edf3;
}

.dark .hljs-operator {
  color: #ff7b72;
}

.dark .hljs-property {
  color: #79c0ff;
}

.dark .hljs-punctuation {
  color: #e6edf3;
}
```

**Step 4: Verify changes work**

Run: `cd /home/ubuntu/workspace/owork/.worktrees/theme-switching/desktop && npm run build 2>&1 | head -20`

Expected: Build starts without CSS parsing errors

**Step 5: Commit**

```bash
git add src/index.css
git commit -m "feat(theme): add CSS variables for light/dark themes"
```

---

## Task 2: Update Tailwind Config

**Files:**
- Modify: `desktop/tailwind.config.js`

**Step 1: Update colors to use CSS variables**

Replace the colors section:

```javascript
/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: 'class',
  theme: {
    extend: {
      fontFamily: {
        sans: ['Space Grotesk', 'system-ui', 'sans-serif'],
      },
      colors: {
        primary: {
          DEFAULT: '#2b6cee',
          hover: '#1d5cd6',
          light: '#3d7ef0',
        },
        // Theme-aware colors using CSS variables
        dark: {
          bg: 'var(--color-bg)',
          card: 'var(--color-card)',
          hover: 'var(--color-hover)',
          border: 'var(--color-border)',
        },
        muted: 'var(--color-muted)',
        status: {
          online: '#22c55e',
          offline: '#6b7280',
          error: '#ef4444',
          warning: '#f59e0b',
          success: '#22c55e',
        },
      },
    },
  },
  plugins: [],
}
```

**Step 2: Commit**

```bash
git add tailwind.config.js
git commit -m "feat(theme): update tailwind config to use CSS variables"
```

---

## Task 3: Create ThemeContext

**Files:**
- Create: `desktop/src/contexts/ThemeContext.tsx`

**Step 1: Create the contexts directory and ThemeContext file**

```typescript
import { createContext, useContext, useEffect, useState, ReactNode } from 'react';

export type ThemeMode = 'light' | 'dark' | 'system';
export type ResolvedTheme = 'light' | 'dark';

interface ThemeContextValue {
  mode: ThemeMode;
  resolvedTheme: ResolvedTheme;
  setMode: (mode: ThemeMode) => void;
}

const ThemeContext = createContext<ThemeContextValue | undefined>(undefined);

const STORAGE_KEY = 'theme-mode';

function getSystemTheme(): ResolvedTheme {
  if (typeof window === 'undefined') return 'dark';
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function applyTheme(theme: ResolvedTheme) {
  const root = document.documentElement;
  if (theme === 'dark') {
    root.classList.add('dark');
  } else {
    root.classList.remove('dark');
  }
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [mode, setModeState] = useState<ThemeMode>(() => {
    if (typeof window === 'undefined') return 'system';
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === 'light' || saved === 'dark' || saved === 'system') {
      return saved;
    }
    return 'system';
  });

  const [resolvedTheme, setResolvedTheme] = useState<ResolvedTheme>(() => {
    if (typeof window === 'undefined') return 'dark';
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === 'light' || saved === 'dark') return saved;
    return getSystemTheme();
  });

  const setMode = (newMode: ThemeMode) => {
    setModeState(newMode);
    localStorage.setItem(STORAGE_KEY, newMode);
  };

  // Apply theme when mode changes
  useEffect(() => {
    const resolved = mode === 'system' ? getSystemTheme() : mode;
    setResolvedTheme(resolved);
    applyTheme(resolved);
  }, [mode]);

  // Listen for system theme changes when in system mode
  useEffect(() => {
    if (mode !== 'system') return;

    const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
    const handler = (e: MediaQueryListEvent) => {
      const newTheme = e.matches ? 'dark' : 'light';
      setResolvedTheme(newTheme);
      applyTheme(newTheme);
    };

    mediaQuery.addEventListener('change', handler);
    return () => mediaQuery.removeEventListener('change', handler);
  }, [mode]);

  return (
    <ThemeContext.Provider value={{ mode, resolvedTheme, setMode }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme() {
  const context = useContext(ThemeContext);
  if (!context) {
    throw new Error('useTheme must be used within a ThemeProvider');
  }
  return context;
}
```

**Step 2: Create index.ts for contexts**

Create `desktop/src/contexts/index.ts`:

```typescript
export { ThemeProvider, useTheme } from './ThemeContext';
export type { ThemeMode, ResolvedTheme } from './ThemeContext';
```

**Step 3: Commit**

```bash
git add src/contexts/
git commit -m "feat(theme): add ThemeContext with system preference detection"
```

---

## Task 4: Update index.html

**Files:**
- Modify: `desktop/index.html`

**Step 1: Remove hardcoded dark class and add FOUC prevention script**

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <link rel="icon" type="image/svg+xml" href="/vite.svg" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Claude Agent Platform</title>
    <script>
      (function() {
        var saved = localStorage.getItem('theme-mode');
        var prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
        var isDark = saved === 'dark' || (saved !== 'light' && prefersDark);
        if (isDark) document.documentElement.classList.add('dark');
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

Note: Removed `class="dark"` from `<html>`, removed hardcoded bg color from `<body>`, added inline FOUC prevention script.

**Step 2: Commit**

```bash
git add index.html
git commit -m "feat(theme): add FOUC prevention script in index.html"
```

---

## Task 5: Wrap App with ThemeProvider

**Files:**
- Modify: `desktop/src/App.tsx`

**Step 1: Import and wrap with ThemeProvider**

```typescript
import { useEffect } from 'react';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ThemeProvider } from './contexts';
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
git add src/App.tsx
git commit -m "feat(theme): wrap App with ThemeProvider"
```

---

## Task 6: Add i18n Strings

**Files:**
- Modify: `desktop/src/i18n/locales/en.json`
- Modify: `desktop/src/i18n/locales/zh.json`

**Step 1: Add theme strings to en.json**

Add inside the `"settings"` object, after `"language"`:

```json
"theme": {
  "title": "Appearance",
  "light": "Light",
  "dark": "Dark",
  "system": "System"
},
```

**Step 2: Add theme strings to zh.json**

Add inside the `"settings"` object, after `"language"`:

```json
"theme": {
  "title": "外观",
  "light": "浅色",
  "dark": "深色",
  "system": "跟随系统"
},
```

**Step 3: Commit**

```bash
git add src/i18n/locales/en.json src/i18n/locales/zh.json
git commit -m "feat(theme): add i18n strings for theme settings"
```

---

## Task 7: Add Theme Selector to Settings Page

**Files:**
- Modify: `desktop/src/pages/SettingsPage.tsx`

**Step 1: Import useTheme hook**

Add at the top with other imports:

```typescript
import { useTheme, ThemeMode } from '../contexts';
```

**Step 2: Add theme state in component**

Inside the `SettingsPage` function, after the `handleLanguageChange` function:

```typescript
const { mode: themeMode, setMode: setThemeMode } = useTheme();

const handleThemeChange = (theme: ThemeMode) => {
  setThemeMode(theme);
};
```

**Step 3: Add theme selection UI**

Add after the Language Settings section (after line ~353):

```tsx
{/* Theme Settings */}
<section className="mb-8 bg-dark-card rounded-lg p-6">
  <h2 className="text-lg font-semibold mb-2">{t('settings.theme.title')}</h2>
  <p className="text-sm text-muted mb-4">{t('settings.language.description')}</p>
  <div className="flex gap-3">
    <button
      onClick={() => handleThemeChange('light')}
      className={`flex-1 px-4 py-3 rounded-lg text-sm font-medium transition-colors flex items-center justify-center gap-2 ${
        themeMode === 'light'
          ? 'bg-primary text-white'
          : 'bg-dark-bg border border-dark-border text-muted hover:border-muted'
      }`}
    >
      <span className="material-symbols-outlined text-lg">light_mode</span>
      {t('settings.theme.light')}
    </button>
    <button
      onClick={() => handleThemeChange('dark')}
      className={`flex-1 px-4 py-3 rounded-lg text-sm font-medium transition-colors flex items-center justify-center gap-2 ${
        themeMode === 'dark'
          ? 'bg-primary text-white'
          : 'bg-dark-bg border border-dark-border text-muted hover:border-muted'
      }`}
    >
      <span className="material-symbols-outlined text-lg">dark_mode</span>
      {t('settings.theme.dark')}
    </button>
    <button
      onClick={() => handleThemeChange('system')}
      className={`flex-1 px-4 py-3 rounded-lg text-sm font-medium transition-colors flex items-center justify-center gap-2 ${
        themeMode === 'system'
          ? 'bg-primary text-white'
          : 'bg-dark-bg border border-dark-border text-muted hover:border-muted'
      }`}
    >
      <span className="material-symbols-outlined text-lg">contrast</span>
      {t('settings.theme.system')}
    </button>
  </div>
</section>
```

**Step 4: Replace hardcoded text colors with theme-aware classes**

In SettingsPage.tsx, replace:
- `text-white` → keep as is (will work with CSS variable)
- `text-gray-400` → `text-muted`
- `text-gray-500` → `text-muted`
- `bg-[#1a1f2e]` → `bg-dark-card`
- `bg-[#101622]` → `bg-dark-bg`
- `border-gray-700` → `border-dark-border`

**Step 5: Commit**

```bash
git add src/pages/SettingsPage.tsx
git commit -m "feat(theme): add theme selector to Settings page"
```

---

## Task 8: Update MarkdownRenderer for Theme-Aware Mermaid

**Files:**
- Modify: `desktop/src/components/common/MarkdownRenderer.tsx`

**Step 1: Import useTheme**

```typescript
import { useTheme } from '../../contexts';
```

**Step 2: Update mermaid initialization to be dynamic**

Remove the static `mermaid.initialize()` call at the top level. Instead, create a function that initializes mermaid with the correct theme:

```typescript
// Mermaid theme configurations
const getMermaidConfig = (isDark: boolean) => ({
  startOnLoad: false,
  theme: isDark ? 'dark' : 'default',
  themeVariables: isDark ? {
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
    primaryTextColor: '#1d1d1f',
    primaryBorderColor: '#d1d1d6',
    lineColor: '#6e6e73',
    secondaryColor: '#f5f5f7',
    tertiaryColor: '#ffffff',
    background: '#ffffff',
    mainBkg: '#ffffff',
    nodeBorder: '#d1d1d6',
    clusterBkg: '#f5f5f7',
    titleColor: '#1d1d1f',
    edgeLabelBackground: '#ffffff',
  },
  fontFamily: 'Space Grotesk, sans-serif',
});

// Initialize mermaid with default dark theme
mermaid.initialize(getMermaidConfig(true));
```

**Step 3: Update MermaidDiagram to reinitialize on theme change**

In the `MermaidDiagram` component, add theme awareness:

```typescript
const MermaidDiagram = memo(function MermaidDiagram({ chart }: { chart: string }) {
  const { resolvedTheme } = useTheme();
  // ... existing state

  useEffect(() => {
    // Reinitialize mermaid when theme changes
    mermaid.initialize(getMermaidConfig(resolvedTheme === 'dark'));
  }, [resolvedTheme]);

  useEffect(() => {
    const renderDiagram = async () => {
      // ... existing render logic
    };
    renderDiagram();
  }, [chart, resolvedTheme]); // Add resolvedTheme as dependency

  // ... rest of component
});
```

**Step 4: Commit**

```bash
git add src/components/common/MarkdownRenderer.tsx
git commit -m "feat(theme): add theme-aware mermaid diagram rendering"
```

---

## Task 9: Update Remaining Components (Batch Replace)

**Files:**
- Multiple component files (29 files with 191 occurrences)

The CSS variable approach means most components will automatically work because `bg-dark-card`, `bg-dark-bg`, etc. now reference CSS variables. However, some hardcoded colors need updating.

**Step 1: Search and update hardcoded colors in components**

Use search-replace for common patterns across all files in `src/`:

| Find | Replace |
|------|---------|
| `text-white` | Keep (will inherit from body) |
| `bg-\[#1a1f2e\]` | `bg-dark-card` |
| `bg-\[#101622\]` | `bg-dark-bg` |
| `border-gray-700` | `border-dark-border` |
| `text-gray-400` | `text-muted` |
| `text-gray-500` | `text-muted` |
| `text-gray-300` | (keep for specific cases) |

Run these commands:

```bash
cd /home/ubuntu/workspace/owork/.worktrees/theme-switching/desktop

# Replace hardcoded background colors
find src -name "*.tsx" -exec sed -i 's/bg-\[#1a1f2e\]/bg-dark-card/g' {} \;
find src -name "*.tsx" -exec sed -i 's/bg-\[#101622\]/bg-dark-bg/g' {} \;

# Replace border colors
find src -name "*.tsx" -exec sed -i 's/border-gray-700/border-dark-border/g' {} \;
find src -name "*.tsx" -exec sed -i 's/border-gray-600/border-dark-border/g' {} \;

# Replace text colors (be careful with these)
find src -name "*.tsx" -exec sed -i 's/text-gray-400/text-muted/g' {} \;
find src -name "*.tsx" -exec sed -i 's/text-gray-500/text-muted/g' {} \;
```

**Step 2: Manually review and fix any issues**

Check files for any rendering issues by running:

```bash
npm run build 2>&1 | grep -i error
```

**Step 3: Commit**

```bash
git add src/
git commit -m "feat(theme): update component colors to use CSS variables"
```

---

## Task 10: Visual Testing and Final Adjustments

**Step 1: Start dev server**

```bash
cd /home/ubuntu/workspace/owork/.worktrees/theme-switching/desktop
npm run dev
```

**Step 2: Test all three modes**

1. Open Settings page
2. Click "Light" - verify:
   - Background changes to light gray
   - Cards are white
   - Text is dark
   - Code blocks have light syntax highlighting
3. Click "Dark" - verify:
   - Background changes to dark blue-gray
   - Cards are dark
   - Text is white
   - Code blocks have dark syntax highlighting
4. Click "System" - verify:
   - Theme follows system preference
   - Changing system preference updates theme

**Step 3: Test FOUC prevention**

1. Set theme to Light
2. Refresh page
3. Verify no dark flash on load

**Step 4: Fix any visual issues found**

Document and fix any components that don't look correct.

**Step 5: Final commit**

```bash
git add -A
git commit -m "feat(theme): complete theme switching implementation"
```

---

## Summary of Changes

| File | Type | Changes |
|------|------|---------|
| `src/index.css` | Modify | CSS variables for light/dark, hljs themes |
| `tailwind.config.js` | Modify | Colors reference CSS variables |
| `src/contexts/ThemeContext.tsx` | Create | Theme state, localStorage, system detection |
| `src/contexts/index.ts` | Create | Export ThemeProvider and useTheme |
| `index.html` | Modify | Remove dark class, add FOUC script |
| `src/App.tsx` | Modify | Wrap with ThemeProvider |
| `src/i18n/locales/en.json` | Modify | Add theme strings |
| `src/i18n/locales/zh.json` | Modify | Add theme strings |
| `src/pages/SettingsPage.tsx` | Modify | Add theme selector UI |
| `src/components/common/MarkdownRenderer.tsx` | Modify | Theme-aware mermaid |
| Multiple components | Modify | Replace hardcoded colors |
