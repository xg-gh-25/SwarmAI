# Web Design Review

Audit frontend code against web interface guidelines, accessibility standards, and design best practices. Produces actionable findings with file:line references.

## Workflow

### Step 1: Identify Files to Review

From the user's request, determine:
- Specific files or glob patterns (e.g., `src/components/*.tsx`)
- If no files specified, ask: "Which files or directory should I review?"

Read all target files before starting the review.

### Step 2: Fetch Current Guidelines

Pull the latest Web Interface Guidelines as the primary checklist:

```
https://raw.githubusercontent.com/vercel-labs/web-interface-guidelines/main/command.md
```

Use WebFetch to retrieve this. If unavailable, fall back to the built-in checklist below.

### Step 3: Run the Audit

Review each file against all categories. For every finding, record:

```
{file}:{line} [{severity}] {category} -- {description}
```

Severity levels:
- **CRITICAL** -- Broken functionality or severe accessibility violation
- **WARNING** -- Degrades UX or violates best practices
- **INFO** -- Improvement opportunity, not a defect

### Review Categories

#### A. Accessibility (WCAG 2.1 AA)

| Check | What to Look For |
|-------|-----------------|
| Color contrast | Text/background ratio >= 4.5:1 (normal), >= 3:1 (large) |
| Alt text | All `<img>` have meaningful `alt` (not "image" or empty on informational images) |
| Keyboard navigation | Interactive elements focusable, visible focus styles, logical tab order |
| ARIA usage | Correct roles, not overriding native semantics, `aria-label` on icon buttons |
| Form labels | Every input has an associated `<label>` or `aria-label` |
| Heading hierarchy | h1 > h2 > h3, no skipped levels |
| Motion | `prefers-reduced-motion` respected for animations |
| Language | `<html lang="...">` set |

#### B. Semantic HTML

| Check | What to Look For |
|-------|-----------------|
| Landmark elements | `<header>`, `<main>`, `<nav>`, `<footer>`, `<aside>` used correctly |
| Lists | Navigation items in `<ul>`/`<ol>`, not bare `<div>` |
| Buttons vs links | `<button>` for actions, `<a>` for navigation |
| Tables | Data tables use `<th>` with scope, not div-based grids |
| Sections | Logical content grouping with `<section>` and `<article>` |

#### C. Responsive Design

| Check | What to Look For |
|-------|-----------------|
| Viewport meta | `<meta name="viewport" content="width=device-width, initial-scale=1">` |
| Breakpoints | Layout works at 375px, 768px, 1200px |
| Touch targets | Interactive elements >= 44x44px on mobile |
| No horizontal scroll | Content doesn't overflow viewport at any width |
| Images | Responsive with `max-width: 100%` or `srcset` |
| Font scaling | Uses `rem`/`em`, not fixed `px` for text |

#### D. Performance

| Check | What to Look For |
|-------|-----------------|
| Image optimization | Appropriate format (WebP/AVIF), lazy loading below fold |
| Font loading | `font-display: swap`, limited font weights |
| CSS efficiency | No unused styles, minimal specificity wars |
| JS size | Minimal JS, defer/async on scripts |
| Layout shifts | Explicit dimensions on images/embeds, no CLS triggers |
| Critical path | Above-fold content doesn't depend on JS to render |

#### E. Design Quality

| Check | What to Look For |
|-------|-----------------|
| Typography | Consistent hierarchy, readable line lengths (45-75 chars), adequate line-height (1.4-1.6) |
| Spacing | Consistent spacing scale, adequate padding on touch targets |
| Color system | Coherent palette, not more than 3-4 colors + neutrals |
| Visual hierarchy | Clear focal point, logical reading flow |
| Consistency | Same patterns used for same concepts throughout |
| Dark mode | If supported: proper color tokens, no hardcoded colors |

#### F. Security (Frontend)

| Check | What to Look For |
|-------|-----------------|
| XSS vectors | No `innerHTML` with user input, no `eval()` |
| External resources | Integrity hashes on CDN scripts (`integrity="sha384-..."`) |
| Sensitive data | No API keys, tokens, or credentials in client code |
| Forms | CSRF protection, secure action URLs |

### Step 4: Generate Report

Present findings grouped by severity, then by category:

```markdown
## UI Review: {files reviewed}

### Summary
- {N} critical, {N} warnings, {N} info
- Overall: {PASS / NEEDS WORK / CRITICAL ISSUES}

### Critical
- `src/App.tsx:42` [CRITICAL] Accessibility -- Button has no accessible name. Add `aria-label` or visible text.
- ...

### Warnings
- `src/components/Hero.tsx:18` [WARNING] Responsive -- Hero image has no `max-width`, will overflow on mobile.
- ...

### Improvements
- `src/styles/main.css:7` [INFO] Performance -- Consider using `font-display: swap` on @font-face.
- ...

### What's Good
- {Highlight 2-3 things done well -- specific, not generic praise}
```

Always include "What's Good" -- review is constructive, not just a bug list.

### Step 5: Offer Fixes

For each CRITICAL and WARNING finding, offer to fix it:
- Show the specific code change needed
- If multiple fixes are straightforward, batch them and offer to apply all at once

---

## Built-in Checklist (Fallback)

If the Vercel Web Interface Guidelines are unavailable, use these core principles:

1. Every interaction should be fast (< 100ms feedback)
2. Every element should be keyboard accessible
3. Every state should be visible (loading, error, empty, success)
4. Every action should be reversible or confirmed
5. Every layout should work from 320px to 2560px
6. Every animation should respect motion preferences
7. Every form should validate inline, not on submit
8. Every error should tell the user what to do next
9. Every page should work without JavaScript for core content
10. Every component should handle edge cases (empty, overflow, error)

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Too many findings | Focus on CRITICAL first, batch related WARNINGs |
| User just wants a quick check | Run only categories A (Accessibility) + C (Responsive) |
| Review scope too broad | Ask user to narrow to specific components or pages |
| Framework-specific patterns | Adapt checks for React/Vue/Svelte idioms (e.g., JSX alt text) |

