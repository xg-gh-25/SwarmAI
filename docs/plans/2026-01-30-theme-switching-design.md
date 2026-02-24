# Theme Switching Feature Design

## Overview

Add light/dark/system theme switching to the Settings page. The existing dark theme remains unchanged; this document defines the new light theme and implementation approach.

## Design Principles

1. **Preserve existing dark theme** - No changes to current dark theme colors
2. **Cold white tone** - Light theme uses blue-gray tones to match the primary color `#2b6cee`
3. **Consistent design language** - Same visual hierarchy and component styles across themes
4. **System preference support** - Respect OS-level dark/light mode setting

---

## Color Palette

### Background Colors

| Usage | CSS Variable | Dark Theme | Light Theme |
|-------|--------------|------------|-------------|
| Main background | `--color-bg` | `#101622` | `#f8fafc` |
| Card/Panel | `--color-card` | `#1a1f2e` | `#ffffff` |
| Hover state | `--color-hover` | `#252b3d` | `#f1f5f9` |
| Border | `--color-border` | `#2d3548` | `#e2e8f0` |

### Primary Colors (Unchanged)

| Usage | CSS Variable | Value |
|-------|--------------|-------|
| Primary | `--color-primary` | `#2b6cee` |
| Primary Hover | `--color-primary-hover` | `#1d5cd6` |
| Primary Light | `--color-primary-light` | `#3d7ef0` |

### Text Colors

| Usage | CSS Variable | Dark Theme | Light Theme |
|-------|--------------|------------|-------------|
| Primary text | `--color-text` | `#ffffff` | `#1e293b` |
| Secondary text | `--color-text-muted` | `#9da6b9` | `#64748b` |
| Placeholder | `--color-placeholder` | `#6b7280` | `#94a3b8` |

### Status Colors

| Status | CSS Variable | Dark Theme | Light Theme |
|--------|--------------|------------|-------------|
| Success | `--color-status-success` | `#22c55e` | `#16a34a` |
| Error | `--color-status-error` | `#ef4444` | `#dc2626` |
| Warning | `--color-status-warning` | `#f59e0b` | `#d97706` |
| Info | `--color-status-info` | `#3b82f6` | `#2563eb` |

---

## Component Specifications

### Input Fields

| Property | CSS Variable | Dark Theme | Light Theme |
|----------|--------------|------------|-------------|
| Background | `--color-input-bg` | `#1a1f2e` | `#ffffff` |
| Border | `--color-input-border` | `#2d3548` | `#cbd5e1` |
| Focus border | `--color-input-focus` | `#2b6cee` | `#2b6cee` |
| Placeholder | `--color-placeholder` | `#6b7280` | `#94a3b8` |

### Buttons (Secondary)

| Property | CSS Variable | Dark Theme | Light Theme |
|----------|--------------|------------|-------------|
| Background | - | `transparent` | `transparent` |
| Border | `--color-btn-secondary-border` | `#2d3548` | `#cbd5e1` |
| Hover background | `--color-btn-secondary-hover` | `#252b3d` | `#f1f5f9` |

### Sidebar

| Property | CSS Variable | Dark Theme | Light Theme |
|----------|--------------|------------|-------------|
| Background | `--color-sidebar-bg` | `#101622` | `#ffffff` |
| Icon default | `--color-sidebar-icon` | `#9da6b9` | `#64748b` |
| Icon active | `--color-sidebar-icon-active` | `#ffffff` | `#2b6cee` |
| Divider | `--color-sidebar-divider` | `#2d3548` | `#e2e8f0` |

### Tool Call Cards (Chat)

| Property | CSS Variable | Dark Theme | Light Theme |
|----------|--------------|------------|-------------|
| Background | `--color-tool-card-bg` | `#1a1f2e` | `#f1f5f9` |
| Border | `--color-tool-card-border` | `#2d3548` | `#e2e8f0` |
| Code block bg | `--color-code-bg` | `#101622` | `#1e293b` |

### File List Panel

| Property | CSS Variable | Dark Theme | Light Theme |
|----------|--------------|------------|-------------|
| Background | `--color-file-panel-bg` | `#1a1f2e` | `#ffffff` |
| Item hover | `--color-file-item-hover` | `#252b3d` | `#f1f5f9` |
| Folder icon | `--color-folder-icon` | `#f59e0b` | `#d97706` |

### Mermaid Diagrams

| Property | Dark Theme | Light Theme |
|----------|------------|-------------|
| Primary color | `#2b6cee` | `#2b6cee` |
| Background | `#1a1f2e` | `#ffffff` |
| Text | `#ffffff` | `#1e293b` |
| Line color | `#9da6b9` | `#64748b` |

---

## Implementation Architecture

### ThemeContext

```typescript
interface ThemeContextType {
  theme: 'light' | 'dark' | 'system';      // User preference
  resolvedTheme: 'light' | 'dark';          // Actual applied theme
  setTheme: (theme: 'light' | 'dark' | 'system') => void;
}
```

### Storage

```typescript
// Persist user preference
localStorage.setItem('theme', theme);
localStorage.getItem('theme'); // Returns 'light' | 'dark' | 'system' | null
```

### CSS Variable Strategy

```css
/* Light theme (default) */
:root {
  --color-bg: #f8fafc;
  --color-card: #ffffff;
  --color-text: #1e293b;
  /* ... */
}

/* Dark theme (class-based override) */
:root.dark {
  --color-bg: #101622;
  --color-card: #1a1f2e;
  --color-text: #ffffff;
  /* ... */
}
```

### Theme Application

```typescript
// Apply theme class to document root
document.documentElement.classList.remove('light', 'dark');
document.documentElement.classList.add(resolvedTheme);
```

### System Preference Detection

```typescript
// Detect OS preference
const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
const systemPreference = mediaQuery.matches ? 'dark' : 'light';

// Listen for changes
mediaQuery.addEventListener('change', (e) => {
  if (theme === 'system') {
    applyTheme(e.matches ? 'dark' : 'light');
  }
});
```

### FOUC Prevention

```html
<!-- In index.html <head> before CSS -->
<script>
  (function() {
    const theme = localStorage.getItem('theme');
    const systemDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    const resolved = theme === 'system' || !theme ? (systemDark ? 'dark' : 'light') : theme;
    document.documentElement.classList.add(resolved);
  })();
</script>
```

---

## Settings UI

### Theme Selector Location

Add theme selector to Settings page, positioned after Language settings:

```
设置 / Settings
├── 语言 / Language
│   └── [中文] [English]
├── 主题 / Theme          ← NEW
│   └── [Light] [Dark] [System]
├── API Configuration
│   └── ...
```

### i18n Keys

```json
{
  "settings": {
    "theme": {
      "title": "主题 / Theme",
      "description": "选择界面显示主题",
      "light": "浅色",
      "dark": "深色",
      "system": "跟随系统"
    }
  }
}
```

---

## Files to Modify

| File | Changes |
|------|---------|
| `desktop/src/index.css` | Add CSS variables with light/dark variants |
| `desktop/src/contexts/ThemeContext.tsx` | Create new ThemeContext provider |
| `desktop/src/App.tsx` | Wrap with ThemeProvider |
| `desktop/src/pages/SettingsPage.tsx` | Add theme selector UI |
| `desktop/src/i18n/locales/en.json` | Add theme translation keys |
| `desktop/src/i18n/locales/zh.json` | Add theme translation keys |
| `desktop/index.html` | Add FOUC prevention script |
| `desktop/src/components/common/MarkdownRenderer.tsx` | Dynamic mermaid theme |
| `desktop/tailwind.config.js` | Update to use CSS variables |

---

## Implementation Steps

1. **Setup CSS Variables** - Define all theme variables in `index.css` with `:root` and `:root.dark` selectors
2. **Create ThemeContext** - Implement theme state management with localStorage persistence
3. **Add FOUC Prevention** - Insert inline script in `index.html`
4. **Update ThemeProvider** - Wrap App with ThemeProvider in `App.tsx`
5. **Add Settings UI** - Implement theme selector in SettingsPage
6. **Add i18n Strings** - Add translation keys for both languages
7. **Update Components** - Replace hardcoded colors with CSS variable classes
8. **Update Mermaid** - Make diagram theme dynamic based on current theme
9. **Testing** - Verify all three modes work correctly across all pages
