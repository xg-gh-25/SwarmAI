# Track J: Interactive Report — Dashboard & Scorecard

> Single-file HTML artifacts with interactive elements: tabs, filters, traffic lights,
> expandable sections, and data visualizations. Branded with direction tokens.
> Built on s_html-artifact's proven template system.

## When This Track Runs

This track executes during BUILD stage when `"interactive_report"` is in
`confirmed_tracks` (from discovery.json). Read this file at BUILD time — you do NOT need the full INSTRUCTIONS.md.

## Inputs Required

- `content/{name}/content_package.md` — Data Layer (metrics, comparisons, time series)
- `content/{name}/discovery.json` — audience, outcome, analysis context
- Active design direction YAML — color tokens for branded styling
- s_html-artifact templates (at `$WORKSPACE/.claude/skills/s_html-artifact/templates/`): `base.css`, `report.html`, `scorecard.html`, `comparison.html`

## Modes (Template Selection)

| Mode | Template Base | Best For | Discovery Signal |
|------|-------------|----------|-----------------|
| **Dashboard** | `report.html` | KPI overview + trends + sections | "dashboard", "overview", "MBR" |
| **Scorecard** | `scorecard.html` | Evaluation cards with ratings | "score", "rate", "assess", "MEDDPICC" |
| **Comparison** | `comparison.html` | Multi-column side-by-side | "compare", "vs", "对比", "选择" |
| **Custom** | `base.css` only | Unique structure | None of the above |

Mode is auto-detected from discovery.json `outcome` + user message context.
If ambiguous, default to Dashboard (most general).

---

## Production Flow

```
Step 1: Select template mode from discovery context

Step 2: Extract data from content_package.md Data Layer
        - Metrics → KPI cards
        - Comparisons → columns or evaluation cards
        - Time series → sparklines or trend indicators
        - Formulas → computed values (live in HTML via JS)

Step 3: Build branded HTML
        - Load base.css design tokens
        - Override with direction YAML tokens (accent, bg, text colors)
        - Populate template with data
        - Add interactive elements (tabs, filters, expandable)

Step 4: Inject interactivity
        - Tab switching (vanilla JS, no framework)
        - Expandable/collapsible sections
        - Traffic light status indicators
        - Sort/filter on tables (if data warrants)

Step 5: Quality verification (RP-J)
```

---

## Step 1: Template Mode Detection

From `discovery.json`:

```python
# Auto-detection heuristic
if "compare" in outcome or "vs" in message:
    mode = "comparison"
elif "score" in outcome or "assess" in outcome:
    mode = "scorecard"
else:
    mode = "dashboard"  # default
```

---

## Step 2: Data Extraction

From `content_package.md` Data Layer, extract:

| Data Type | HTML Element | Interactive Feature |
|-----------|-------------|-------------------|
| **Metrics** (name, value, trend) | KPI card with delta indicator | Hover tooltip with source |
| **Comparisons** (A vs B) | Multi-column grid | Tab switch between perspectives |
| **Time Series** (trend data) | CSS sparkline or mini-chart | None (static visualization) |
| **Status** (RAG indicators) | Traffic light dot | Expandable detail on click |
| **Scores** (1-10 or %) | Progress bar or gauge | Score breakdown on expand |

**Pre-condition:** If Data Layer is empty or has <3 data points:
- Check if Core Layer has enough structure for a qualitative dashboard
- If yes: build text-heavy dashboard with status indicators
- If no: FAIL with message "Track J requires data — consider Track H (document) instead"

---

## Step 3: Build Branded HTML

### Token Override System

Load `base.css` from s_html-artifact as foundation. Override with direction tokens:

```css
/* Direction token overrides (injected after base.css) */
:root {
  /* From direction YAML → override base.css defaults */
  --color-accent: {tokens.accent};          /* was: var(--clay) */
  --color-bg: {tokens.bg-deep};             /* was: var(--ivory) */
  --color-surface: {tokens.bg-elevated};    /* was: var(--paper) */
  --color-text: {tokens.text-primary};      /* was: var(--slate) */
  --color-text-secondary: {tokens.text-secondary};
  --color-border: {tokens.border};
}
```

**Conflict resolution:** If direction tokens conflict with base.css semantic colors
(e.g., direction has dark background but base.css assumes light), flip the entire
scheme using a `.dark-mode` class on `<body>`.

### Template Population

**Dashboard mode** — sections + KPI cards:
```html
<header class="report-header">
  <h1>{title}</h1>
  <p class="subtitle">{content_package.thesis}</p>
  <div class="meta">{date} | {audience} | {data_source}</div>
</header>

<section class="kpi-grid">
  <!-- One card per metric from Data Layer -->
  <div class="kpi-card">
    <span class="kpi-label">{metric.name}</span>
    <span class="kpi-value">{metric.value}</span>
    <span class="kpi-delta {up|down|flat}">{metric.delta}</span>
  </div>
</section>

<section class="content-tabs">
  <!-- One tab per major section from Core Layer key_points -->
</section>
```

**Scorecard mode** — evaluation cards:
```html
<div class="scorecard-grid">
  <div class="score-card">
    <h3>{dimension.name}</h3>
    <div class="score-gauge" data-score="{dimension.score}" data-max="10"></div>
    <p class="score-evidence">{dimension.evidence}</p>
    <span class="score-status {green|yellow|red}">{dimension.status}</span>
  </div>
</div>
```

**Comparison mode** — multi-column:
```html
<div class="comparison-grid" data-columns="{n}">
  <div class="comparison-header">
    <div class="col-header">{option_a.name}</div>
    <div class="col-header">{option_b.name}</div>
  </div>
  <!-- One row per comparison dimension -->
  <div class="comparison-row">
    <span class="row-label">{dimension}</span>
    <div class="col-value">{option_a.value}</div>
    <div class="col-value">{option_b.value}</div>
  </div>
</div>
```

---

## Step 4: Interactivity Injection

All interactivity is **vanilla JavaScript** — no frameworks, no CDN, no dependencies.
The HTML file must be fully self-contained.

### Tab Switching

```javascript
document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById(btn.dataset.tab).classList.add('active');
  });
});
```

### Expandable Sections

```javascript
document.querySelectorAll('.expandable-header').forEach(header => {
  header.addEventListener('click', () => {
    header.parentElement.classList.toggle('expanded');
  });
});
```

### Traffic Light Indicators

CSS-only (no JS needed):
```css
.status-dot { width: 12px; height: 12px; border-radius: 50%; }
.status-dot.green { background: var(--color-success); }
.status-dot.yellow { background: var(--color-warning); }
.status-dot.red { background: var(--color-danger); }
```

---

## Step 5: Quality Verification (RP-J)

### RP-J1: Data Completeness

- [ ] ALL metrics from Data Layer are represented (no cherry-picking)
- [ ] Each metric shows: value + unit + trend/delta
- [ ] Data sources attributed (footer or tooltip)
- [ ] "As of" timestamp present

### RP-J2: Template Integrity

- [ ] Chosen template mode matches the data structure
- [ ] All template sections populated (no empty placeholder cards)
- [ ] base.css loaded AND direction overrides applied
- [ ] No broken CSS variables (check for unresolved `{tokens.xxx}`)

### RP-J3: Interactivity Works

- [ ] Open HTML in browser (or Playwright) — all tabs switch correctly
- [ ] Expandable sections expand/collapse
- [ ] No JavaScript errors in console
- [ ] Works without network (fully self-contained)

### RP-J4: Brand Consistency

- [ ] Color palette matches active direction (not base.css defaults if direction exists)
- [ ] Typography follows base.css (serif headings, sans body)
- [ ] Footer includes generation timestamp + "Generated by {{PROJECT_NAME}}" (or omit if unbranded)
- [ ] No clashing colors between base.css palette and direction overrides

### RP-J5: Accessibility

- [ ] All images have alt text
- [ ] Color contrast ratio ≥ 4.5:1 for text
- [ ] Tab navigation works (focusable elements)
- [ ] Status indicators have text label (not color-only)

### RP-J6: Print Friendliness

- [ ] `@media print` styles defined (hide tabs, expand all sections)
- [ ] No horizontal overflow on A4/Letter
- [ ] Traffic lights print as text labels

---

## Output Files

```
content/{name}/tracks/interactive-report/
├── {topic}-report.html    — single-file interactive HTML (ALWAYS produced)
└── screenshot.png         — Playwright screenshot for chat preview (if available)
```

## Relationship to s_html-artifact

Track J uses s_html-artifact's **templates** as starting points, NOT the skill itself.
The skill's trigger conditions and escalation logic do not apply — Track J always
produces HTML (it IS the confirmed format). What we reuse:

| From s_html-artifact | How Track J uses it |
|---------------------|-------------------|
| `base.css` | Foundation design system (overridden by direction tokens) |
| `report.html` | Dashboard template structure |
| `scorecard.html` | Evaluation card layout pattern |
| `comparison.html` | Multi-column grid structure |
| P0 constraint (inline markdown) | **Does NOT apply** — Track J's output IS the artifact |
