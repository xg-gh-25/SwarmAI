---
name: Frontend Design
description: >
  Create production-grade frontend interfaces, landing pages, and interactive web experiences.
  TRIGGER: "build a landing page", "create a website", "design a UI", "frontend prototype", "interactive page", "HTML page", "web app mockup".
  DO NOT USE: for backend APIs (just code directly) or for reviewing existing UI (use web-design-review).
version: "1.0.0"
---

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

## Design Philosophy

### Avoid Generic AI Aesthetics

The biggest risk with AI-generated UI is looking like every other AI-generated UI. Before writing code:

1. **Choose a bold aesthetic direction** -- don't default to "clean and modern"
2. **Commit to it fully** -- half-measures look worse than generic
3. **Make at least one unexpected choice** -- typography, layout, animation, color

### Aesthetic Directions

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

### Step 2: Design Before Coding

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
