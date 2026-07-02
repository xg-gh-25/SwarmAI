# Anti-AI-Slop Rules

> The biggest risk with AI-generated design is looking like *every other* AI-generated
> design — "on distribution," forgettable, generic. These rules exist to push output
> OFF the generic centroid. Consult before generating any slide deck, landing page, or
> visual artifact.
>
> Source: frontend-slides (MIT). See `data/ATTRIBUTION.md`.

## Banned Patterns (do NOT do these)

### Fonts
- ❌ **Overused display fonts**: Inter, Roboto, Arial, system-ui as a *display/heading*
  face. They read as "default," not "chosen."
- ❌ **Over-converged "safe bold"**: even Space Grotesk is now over-used as the reflex
  bold sans — vary deliberately (see the preset + bold-template font pairings for
  distinctive alternatives).

### Colors
- ❌ **Generic indigo** `#6366f1` (and the whole indigo-500 reflex) as the primary.
- ❌ **Purple gradients on white** — the single most recognizable "AI slop" signature.
- ❌ Cliché blue→purple SaaS gradients used without a reason.

### Layouts
- ❌ **All-centered everything** — hero centered, cards centered, CTA centered. Reads
  as a template, not a composition.
- ❌ **Identical card grids** — N equal cards in a uniform grid with no hierarchy.
- ❌ **Cookie-cutter dashboard look** — the generic stat-card + chart + sidebar layout
  applied where it isn't earned.

### Decoration
- ❌ **Realistic illustrations / stock-style spot illustrations** — use abstract or
  geometric CSS shapes instead.
- ❌ **Gratuitous glassmorphism** — frosted blur used as decoration, not function.

## Positive Counter-Rules (do THIS instead)

- ✅ **Distinctive typography** — commit to a real typeface pairing with character
  (pull from `slide_presets.csv` / `slide_bold_templates.csv` / `typography.csv`).
- ✅ **Cohesive palette with dominant + sharp accent** — one dominant color that owns
  the composition, one or two sharp accents that punctuate. Not a rainbow, not a
  timid monochrome.
- ✅ **Atmospheric, layered backgrounds** — gradient mesh, subtle noise, or a grid
  pattern for depth (see `animation_feelings.csv` "Background Effect"). Depth > flat.
- ✅ **Context-specific choices that surprise and delight** — pick the aesthetic that
  fits *this* content and audience, not the safe default.
- ✅ **Deliberate light/dark + aesthetic variance** — vary between light and dark and
  between aesthetics across projects on purpose; don't default to one look every time.
- ✅ **Abstract shapes only** for decoration (Dark Botanical / Vintage Editorial in the
  presets specifically restrict decoration to abstract/geometric CSS shapes).

## Style-Discovery Discipline (show, don't tell)

When offering the user visual directions, mix: **1 safe preset + at least 1 bold
template + 1 wildcard** — never three variations of the same safe look. Preview
authenticity is a hard rule: a preview must look like a *real first slide*, never a
diagnostic card with template names / file paths / requirement notes on it.

## CSS Gotcha (carried from source)

You cannot negate a CSS function with a leading minus (`-clamp(...)`) — the browser
silently discards the entire declaration with no error. Wrap it: `calc(-1 * clamp(...))`.
