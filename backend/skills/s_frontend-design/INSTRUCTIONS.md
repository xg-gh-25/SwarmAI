# Frontend Design

Create high-quality, production-ready frontend interfaces that are visually striking and functionally complete. Every output should be a self-contained HTML file that works in a browser.

## Output Location

Save generated files to:
```
~/.swarm-ai/SwarmWS/Projects/<project-name>/
```

For standalone pages / quick prototypes:
```
~/.swarm-ai/SwarmWS/Knowledge/Notes/prototypes/
```

Entry file must always be named `index.html`.

## Design Intelligence Database

This skill includes a searchable design knowledge base (67 UI styles, 161 color palettes, 57 font pairings, 161 industry reasoning rules, 99 UX guidelines, 25 chart types). Use it **before coding** to make informed design decisions.

### Scripts & Entry Points

All scripts are in the `scripts/` directory relative to this skill. Run from the skill directory.

| Script | Purpose | Usage |
|--------|---------|-------|
| `search.py` | BM25 search across all design databases | `python3 scripts/search.py "<query>" [--domain <d>]` |
| `search.py --design-system` | Generate complete design system recommendation | `python3 scripts/search.py "<query>" --design-system -p "ProjectName"` |

**Search domains:** `style`, `color`, `chart`, `landing`, `product`, `ux`, `typography`

**Examples:**
```bash
# Generate a full design system for a product type
python3 scripts/search.py "beauty spa wellness" --design-system -p "Serenity Spa" -f markdown

# Search for a specific style
python3 scripts/search.py "glassmorphism" --domain style

# Find typography pairings
python3 scripts/search.py "elegant serif" --domain typography

# Get industry-specific color palette
python3 scripts/search.py "fintech banking" --domain color

# Check UX guidelines
python3 scripts/search.py "touch target accessibility" --domain ux
```

### Data Files

| File | Records | Content |
|------|---------|---------|
| `data/ui-reasoning.csv` | 161 | Industry-specific rules: style, color mood, typography, effects, anti-patterns per product type |
| `data/styles.csv` | 67 | UI styles with keywords, colors, effects, performance, accessibility, CSS variables |
| `data/colors.csv` | 161 | Full color systems (primary, secondary, accent, background, muted, border, destructive, ring) |
| `data/typography.csv` | 57 | Font pairings with Google Fonts URLs, CSS imports, Tailwind config |
| `data/ux-guidelines.csv` | 99 | Do/Don't rules with code examples, severity, platform specificity |
| `data/landing.csv` | 24 | Landing page patterns with section order, CTA placement, conversion strategy |
| `data/products.csv` | 161 | Product type → style + color + layout recommendations |
| `data/charts.csv` | 25 | Chart type selection by data type with accessibility notes |

## Design Philosophy

### Avoid Generic AI Aesthetics

The biggest risk with AI-generated UI is looking like every other AI-generated UI. Before writing code:

1. **Run the Design System Generator** -- `python3 scripts/search.py "<product description>" --design-system -p "Name"` to get industry-specific recommendations
2. **Commit to the recommended style fully** -- half-measures look worse than generic
3. **Follow anti-patterns** -- the database tells you what NOT to do for each industry

### Aesthetic Directions (67 styles in database)

The full style database has 67 entries. Here are the most common starting points:

| Direction | Characteristics | Good For |
|-----------|----------------|----------|
| **Brutalist** | Raw, bold typography, stark contrast, visible grid | Developer tools, manifestos, statements |
| **Glassmorphism** | Frosted panels, transparency, gradients, blur | Dashboards, SaaS landing pages |
| **Neo-Retro** | CRT effects, monospace fonts, terminal vibes | Tech/hacker audience, dev blogs |
| **Editorial** | Magazine-quality type hierarchy, generous whitespace | Content-heavy pages, portfolios |
| **Maximalist** | Dense, layered, animated, information-rich | Data dashboards, creative portfolios |
| **Minimal Swiss** | Grid-based, Helvetica/system fonts, restrained color | Corporate, B2B, documentation |
| **Organic** | Soft shapes, nature palette, fluid animations | Wellness, creative, lifestyle brands |
| **Dark Premium** | Rich blacks, luminous accents, elegant typography | Luxury, fintech, premium products |
| **Playful** | Rounded shapes, bright colors, micro-interactions | Consumer apps, onboarding, kids |
| **Cinematic** | Large hero images, minimal text, dramatic lighting | Product launches, storytelling |

For the full 67 styles with CSS variables, implementation checklists, and AI prompt keywords, search the database:
```bash
python3 scripts/search.py "<your style>" --domain style
```

## Workflow

### Step 1: Understand the Brief

Extract from the user's request:

| Dimension | Question | Default |
|-----------|----------|---------|
| **Purpose** | What is this page for? | Landing page |
| **Audience** | Who will see this? | General |
| **Tone** | Professional, playful, bold, minimal? | Match audience |
| **Content** | What sections/content are needed? | Infer from purpose |
| **Interactions** | Any animations, forms, dynamic behavior? | Subtle animations |
| **Responsive** | Mobile-first? Desktop-only? Both? | Both |
| **Tech constraints** | Single HTML? React? Tailwind? | Single HTML + inline CSS/JS |

### Step 2: Generate Design System (NEW)

**Before writing any code**, run the design system generator:

```bash
python3 scripts/search.py "<product description keywords>" --design-system -p "ProjectName" -f markdown
```

This outputs: **pattern** (page structure + CTA placement), **style** (with effects and CSS keywords), **colors** (full semantic palette with CSS variables), **typography** (heading + body fonts with Google Fonts URL), **key effects**, **anti-patterns to avoid**, and a **pre-delivery checklist**.

Use the output to populate Step 2 decisions. If the user specifies a style preference, search that style directly:
```bash
python3 scripts/search.py "glassmorphism" --domain style
```

**If the design system generator is unavailable** (e.g., Python not accessible), fall back to the manual definitions below.

### Step 2 (Fallback): Design Before Coding

Before writing any code, define:

**Typography:**
- Primary font (headings) -- avoid default sans-serif
- Body font -- optimize for readability
- Use Google Fonts CDN or system font stacks
- Define a clear hierarchy: h1 > h2 > h3 > body > caption

**Color System:**
```
Primary:    {dominant brand color}
Secondary:  {supporting color}
Accent:     {call-to-action, highlights}
Background: {page background}
Surface:    {card/panel backgrounds}
Text:       {primary text color}
Muted:      {secondary text, captions}
```

**Layout:**
- Define the grid (12-col, asymmetric, masonry, etc.)
- Plan responsive breakpoints (mobile: 375px, tablet: 768px, desktop: 1200px)
- Identify the visual hierarchy -- what does the eye hit first?

### Step 3: Build

**Single-file approach** (default for prototypes):
- One `index.html` with `<style>` and `<script>` blocks
- Use CDN links for fonts, icons, and lightweight libraries
- Tailwind via CDN play script is acceptable for rapid prototyping

**Multi-file approach** (for production handoff):
- `index.html` -- structure
- `styles.css` -- all styles
- `script.js` -- interactions
- `assets/` -- images, icons

**Code quality rules:**
- Semantic HTML5 (`<header>`, `<main>`, `<section>`, `<article>`, `<footer>`)
- CSS custom properties for the color system and spacing scale
- Smooth animations with `prefers-reduced-motion` respect
- No inline styles in multi-file mode
- Comment sections that aren't self-explanatory

### Step 4: Interactive Elements

When the design includes interactivity:

| Element | Implementation |
|---------|---------------|
| Scroll animations | Intersection Observer API |
| Smooth transitions | CSS transitions + `will-change` |
| Dark/light toggle | CSS custom properties + `prefers-color-scheme` |
| Form validation | HTML5 validation + subtle JS enhancement |
| Navigation | Scroll-spy or smooth scroll anchors |
| Modals/drawers | `<dialog>` element or minimal JS |
| Parallax | `transform: translate3d()` on scroll |

Keep JavaScript minimal. CSS-first for all animations and transitions.

### Step 5: Responsive & Accessibility

**Responsive checklist:**
- [ ] Readable at 375px width (mobile)
- [ ] Touch targets >= 44px
- [ ] No horizontal scroll at any breakpoint
- [ ] Images are responsive (`max-width: 100%`)
- [ ] Font sizes scale appropriately

**Accessibility baseline:**
- [ ] All images have `alt` text
- [ ] Color contrast ratio >= 4.5:1 for text
- [ ] Keyboard navigation works (tab order, focus styles)
- [ ] `aria-label` on interactive elements without visible text
- [ ] `<html lang="...">` set correctly

### Step 6: Polish & Deliver

Before delivering:
- [ ] Test in browser (open file or `python -m http.server`)
- [ ] Check mobile responsive (browser dev tools)
- [ ] Verify all links and interactions work
- [ ] Optimize any large images
- [ ] Remove placeholder/lorem ipsum content

---

## Component Patterns

### Hero Section
```
[Full-width hero]
  - Compelling headline (5-8 words)
  - Supporting subtext (1-2 sentences)
  - Primary CTA button
  - Optional: background image/gradient, floating elements, subtle animation
```

### Feature Grid
```
[3-4 column grid at desktop, stacks on mobile]
  - Icon or illustration
  - Feature name (2-3 words)
  - Description (1-2 sentences)
```

### Social Proof
```
[Testimonials or logos]
  - Avatar + name + role
  - Quote (2-3 sentences max)
  - Or: logo strip with grayscale filter
```

### Pricing Table
```
[2-3 tier comparison]
  - Tier name + price
  - Feature list with check/cross
  - Highlighted "recommended" tier
  - CTA per tier
```

---

## Platform-Specific Templates

### WeChat Article HTML
- Max width: 600px, centered
- Inline all CSS (WeChat strips `<style>` tags)
- No JavaScript
- Images: hosted externally, `<img>` with explicit width
- Font: system default, don't use web fonts

### Email Template
- Table-based layout for compatibility
- Inline CSS only
- Max width: 600px
- Test with Litmus/Email on Acid patterns

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Design looks generic | Go back to Step 2, pick a bolder aesthetic direction |
| Too much JavaScript | Refactor to CSS transitions/animations first |
| Mobile layout broken | Start mobile-first, add desktop overrides |
| Fonts not loading | Check CDN URL, add `font-display: swap` |
| Performance issues | Lazy-load images, minimize DOM nodes, use CSS `contain` |
| User wants React/Vue | Scaffold with Vite: `npm create vite@latest` |

