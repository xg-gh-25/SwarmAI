# Theme Switching Design

## Overview

Add light theme support with three modes: Light, Dark, and System (auto-detect).

## Requirements

- **Light theme style**: System-native appearance (similar to macOS/Windows apps)
- **Switch location**: Settings page only (keep UI clean)
- **Storage**: localStorage (persisted in Tauri app data directory)
- **Modes**: Light / Dark / System

## Architecture

### Theme Mode Logic

| User Selection | Behavior |
|---------------|----------|
| Light | Remove `dark` class from `<html>` |
| Dark | Add `dark` class to `<html>` |
| System | Listen to `prefers-color-scheme` media query, auto-switch |

### New Files

```
desktop/src/
├── contexts/
│   └── ThemeContext.tsx    # Theme state management & provider
```

### Modified Files

| File | Changes |
|------|---------|
| `index.css` | Add semantic CSS variables with light/dark values |
| `tailwind.config.js` | Reference CSS variables for colors |
| `App.tsx` | Wrap with ThemeProvider |
| `index.html` | Remove hardcoded `dark` class, add FOUC prevention script |
| `SettingsPage.tsx` | Add theme selection UI |
| `src/i18n/locales/en.json` | Add theme-related strings |
| `src/i18n/locales/zh.json` | Add theme-related strings |

## Color Scheme

### Semantic CSS Variables

```css
:root {
  /* Backgrounds */
  --color-bg-primary: #f5f5f7;
  --color-bg-card: #ffffff;
  --color-bg-hover: #e5e5e7;
  --color-border: #d1d1d6;

  /* Text */
  --color-text-primary: #1d1d1f;
  --color-text-muted: #6e6e73;

  /* Scrollbar */
  --color-scrollbar-track: #f0f0f0;
  --color-scrollbar-thumb: #c1c1c1;
  --color-scrollbar-thumb-hover: #a1a1a1;
}

.dark {
  /* Backgrounds */
  --color-bg-primary: #101622;
  --color-bg-card: #1a1f2e;
  --color-bg-hover: #252b3d;
  --color-border: #2d3548;

  /* Text */
  --color-text-primary: #ffffff;
  --color-text-muted: #9da6b9;

  /* Scrollbar */
  --color-scrollbar-track: #1a1f2e;
  --color-scrollbar-thumb: #2d3548;
  --color-scrollbar-thumb-hover: #9da6b9;
}
```

### Unchanged Colors

These colors work well on both light and dark backgrounds:

- Primary: `#2b6cee`
- Primary hover: `#1d5cd6`
- Status online/success: `#22c55e`
- Status error: `#ef4444`
- Status warning: `#f59e0b`
- Status offline: `#6b7280`

## Implementation Details

### ThemeContext.tsx

```typescript
import { createContext, useContext, useEffect, useState, ReactNode } from 'react';

type ThemeMode = 'light' | 'dark' | 'system';
type ResolvedTheme = 'light' | 'dark';

interface ThemeContextValue {
  mode: ThemeMode;
  resolvedTheme: ResolvedTheme;
  setMode: (mode: ThemeMode) => void;
}

const ThemeContext = createContext<ThemeContextValue | undefined>(undefined);

const STORAGE_KEY = 'theme-mode';

function getSystemTheme(): ResolvedTheme {
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
    const saved = localStorage.getItem(STORAGE_KEY);
    return (saved as ThemeMode) || 'system';
  });

  const [resolvedTheme, setResolvedTheme] = useState<ResolvedTheme>(() => {
    const saved = localStorage.getItem(STORAGE_KEY) as ThemeMode;
    if (saved === 'light' || saved === 'dark') return saved;
    return getSystemTheme();
  });

  const setMode = (newMode: ThemeMode) => {
    setModeState(newMode);
    localStorage.setItem(STORAGE_KEY, newMode);
  };

  // Apply theme when mode or system preference changes
  useEffect(() => {
    const resolved = mode === 'system' ? getSystemTheme() : mode;
    setResolvedTheme(resolved);
    applyTheme(resolved);
  }, [mode]);

  // Listen for system theme changes
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

### FOUC Prevention Script (index.html)

Add this inline script in `<head>` before any stylesheets:

```html
<script>
  (function() {
    const saved = localStorage.getItem('theme-mode');
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    const isDark = saved === 'dark' || (saved !== 'light' && prefersDark);
    if (isDark) document.documentElement.classList.add('dark');
  })();
</script>
```

### Settings Page UI

Add a new section in SettingsPage.tsx:

```typescript
const themeOptions = [
  { value: 'light', label: t('settings.theme.light'), icon: 'light_mode' },
  { value: 'dark', label: t('settings.theme.dark'), icon: 'dark_mode' },
  { value: 'system', label: t('settings.theme.system'), icon: 'contrast' },
];
```

UI: Three buttons or radio buttons in a row, showing icon + label.

### i18n Strings

**en.json:**
```json
{
  "settings": {
    "theme": {
      "title": "Appearance",
      "light": "Light",
      "dark": "Dark",
      "system": "System"
    }
  }
}
```

**zh.json:**
```json
{
  "settings": {
    "theme": {
      "title": "外观",
      "light": "浅色",
      "dark": "深色",
      "system": "跟随系统"
    }
  }
}
```

### Tailwind Config Update

Update `tailwind.config.js` to use CSS variables:

```javascript
colors: {
  // Semantic colors (theme-aware)
  background: 'var(--color-bg-primary)',
  card: 'var(--color-bg-card)',
  hover: 'var(--color-bg-hover)',
  border: 'var(--color-border)',
  foreground: 'var(--color-text-primary)',
  muted: 'var(--color-text-muted)',

  // Static colors (unchanged)
  primary: {
    DEFAULT: '#2b6cee',
    hover: '#1d5cd6',
    light: '#3d7ef0',
  },
  status: {
    online: '#22c55e',
    offline: '#6b7280',
    error: '#ef4444',
    warning: '#f59e0b',
    success: '#22c55e',
  },
}
```

### Component Updates

Most components use `bg-dark-*` classes. Strategy:

1. **Global replace** class names:
   - `bg-dark-bg` → `bg-background`
   - `bg-dark-card` → `bg-card`
   - `bg-dark-hover` → `bg-hover`
   - `border-dark-border` → `border-border`
   - `text-muted` stays as is (already semantic)

2. **Scrollbar styles** in index.css: use CSS variables

3. **Code syntax highlighting**: add light theme variant (e.g., highlight.js github theme)

4. **Mermaid diagrams**: detect theme and use appropriate config

## Implementation Checklist

1. [ ] Update `index.css` with CSS variables for light/dark themes
2. [ ] Update `tailwind.config.js` to reference CSS variables
3. [ ] Create `ThemeContext.tsx` with provider and hook
4. [ ] Update `index.html`: remove `dark` class, add FOUC prevention script
5. [ ] Wrap App with ThemeProvider in `App.tsx`
6. [ ] Add theme selection UI in `SettingsPage.tsx`
7. [ ] Add i18n strings for theme options
8. [ ] Update component class names (global search/replace)
9. [ ] Add light theme for code syntax highlighting
10. [ ] Update Mermaid diagram theme detection
11. [ ] Test all three modes work correctly
12. [ ] Test FOUC prevention on page load
