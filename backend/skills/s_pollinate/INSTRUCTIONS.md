# Pollinate: instructions

Pollinate owns content that is **revised again and again**: decks, posters, long images and
reports, and platform copy. You write the content and the HTML. The scripts decide whether a
version can ship: it renders, the craft checks pass, and the revise guard passes. Reviewers
only find problems; they never decide.

You manage only the two ends:

- **The front end is the brief.** Know who it is for, the one thesis, and the scope (cards/brief.md).
- **The back end is checks, review and delivery.** Cut a version, review it (at most 2
  rounds), and hand it over in a form the person can copy (cards/cut.md, cards/deliver.md).

The middle (how you write the HTML, how you lay out a slide) is your craft. Use the kind page.

## 1. Route by intent

Read the request, then pick one path. Do not ask which path to use.

| The person wants | Path | Read |
|---|---|---|
| One poster or one post, made once ("做张海报", "write a tweet about X") | **Quick path** (below) | kinds/poster.md or kinds/copy.md |
| A deck, slides, an HTML deck, 演示文稿 | Work flow | cards/brief.md, kinds/deck.md |
| A long image (长图) or a report (报告, dashboard, scorecard) | Work flow | cards/brief.md, kinds/page.md |
| Several kinds from one message (a deck plus a 小红书 post) | Work flow, one work per kind | cards/brief.md, then each kind page |
| A change to something that exists ("改这份 deck", "revise this deck", "第 3 页字太小") | Revise loop | cards/revise.md |
| A video | Not in this version. Video comes in phase 2; say so and offer a deck or poster instead. | — |
| Read or edit a .pptx file itself (fix a typo inside a .pptx) | Not Pollinate: use s_pptx | — |
| A Word or Excel file | Not Pollinate: use s_docx or s_xlsx | — |

A deck is always made in HTML. PPTX is an export of the HTML deck, never the source.
If the person gives a .pptx and asks for a new deck, take its text and images as content and
make an HTML deck; if they give a .pptx template, use it at export (kinds/deck.md § PPTX).

**Quick path (one poster or one post).** No brief and no questions. Decide the defaults
yourself and say them in the delivery paragraph. Then:

1. `work.py init --dir D --kind poster --title T --size 1080x1440` (for a post: `--kind copy`
   and no `--size`).
2. Write `src/index.html` (poster) or `src/copy.md` (copy), following the kind page.
3. `work.py cut --dir D`. Fix what fails, cut again, deliver (cards/deliver.md).

The quick path still makes a work, so the next "change the title" is a normal revise.

## 2. The work

**A work is any folder that holds a `work.json`.** It is registered where it is; it never
moves. The registry lives at `<workspace>/.pollinate/works.json` and stores only paths
(`<workspace>` is `$SWARM_WORKSPACE`, or `~/.swarm-ai/SwarmWS`).

```
<work>/
  work.json         kind, title, style, size, slide order (stable ids), current version, versions
  brief.md          the brief (cards/brief.md); not part of the source
  src/              the source: deck.html, or index.html (poster, page), or copy.md
                    plus shared.css and assets/
  .versions/v003/   a full copy of src/ for that version, with checks.json, guard.json, measure.json
  out/              outputs of the current version only (can be rebuilt; gitignored)
  review/           review evidence of the current version: contact sheet, full frames (gitignored)
```

- **Where a new work goes.** Under the current project: `Projects/<P>/assets/<slug>/`.
  With no project: `Knowledge/Pollinate/<yyyy-mm-dd>-<slug>/`.
- **An existing deck or poster** (anywhere, also outside the workspace): register it in
  place with `work.py adopt`. Never copy it somewhere else first.
- **A work outside the workspace** works the same, but Canvas cannot open it: open the
  output in the browser instead. The delivery block is the same.
- **Slides have stable ids, not positions.** A deck slide is
  `<section data-slide-id="s-7f3a">` (4–8 lowercase hex digits, unique in the work, never
  reused). "Slide 3" is wrong after a merge or an insert, so the guard and the scope use
  ids. Show them to the person as "slide 3 (s-7f3a)". A poster or page is one unit with id
  `main`; files in `src/` other than the main source count as `_shared`.
- **A version is cut only when you hand it over** (render + checks + guard + delivery), not
  on every save. Old versions keep only their source (small); any old version can be
  rendered again.
- **Never edit `work.json` by hand.** Only `work.py` writes it.

## 3. Commands

Run every command as `"$SWARM_BACKEND_BIN" py "$SKILL_DIR/scripts/work.py" <command> …`,
where `$SKILL_DIR` is this skill's base directory (shown when the skill loads).

| Command | What it does |
|---|---|
| `work.py init --dir D --kind K --title T [--style S] [--size 1920x1080]` | Creates the folder, `work.json` and an empty source file, and registers the work |
| `work.py adopt --dir D --kind K [--src <existing html>]` | Registers an existing work in place; adds missing slide ids; makes v001 (`by: user`) |
| `work.py open --dir D --scope id,id [--add N] [--remove id,id] [--note "…"]` | Absorbs hand edits first: if the files on disk differ from the current version, saves them as `vNNN-user` (`by: user`) and uses that as the base; records the open scope |
| `work.py cut --dir D [--formats html,pdf,png,pptx]` | Render → measure → craft checks → guard → write a new version; updates `current` only if all pass; clears the open scope |
| `work.py render --dir D [--formats …]` | Renders the current source to `out/` only; no new version |
| `work.py list` | Prints the registry |

`K` is `deck`, `poster`, `page` or `copy`. Exit codes for every script: **0** passed,
**1** a check or the guard failed (read the message, fix, cut again), **2** usage or
environment error (fix the command or the setup; do not "fix" the content).

Fixed outputs in `out/`: `deck.html` (self-contained: CSS, images and fonts inlined, every
slide visible with JavaScript off), `deck.pdf`, `slides/NN-<id>.png` (NN from 01, in slide
order, 1× canvas size), `deck.pptx`; a poster gives `poster.png` (and `poster@<w>x<h>.png`);
a page gives `page.png`.

The renderer is Playwright Chromium with the viewport set to the work's `size`. The first
time it meets a web font CSS link, it downloads the CSS and font files to
`~/.swarm-ai/cache/fonts/` and rewrites them to local files; after that it blocks every
network request while rendering. So keep a design system's font `<link>` in the source.
If Playwright or Chromium is missing, `work.py` exits 2; install them in user space
(`python3 -m pip install --user playwright`, then `python3 -m playwright install chromium`),
never with sudo.

Other scripts you may need: `craft_checks.py` (run by `cut`; run it yourself only to compare
with a reference deck, kinds/deck.md), `export_pptx.py` (run by `cut`; run it yourself only
with a .pptx template, kinds/deck.md), `recommend_systems.py` (deck style gallery),
`brand_config.py` (brand signature), `p2_scan.py` and `geo_score.py` (copy checks).

## 4. The flow

**New work:** brief (cards/brief.md) → `init` → write the source (kind page) → `cut`
(cards/cut.md) → review (§6) → deliver (cards/deliver.md).

**Revise loop** (every round after the first, cards/revise.md):

1. **Open with a declared scope.** Map the person's words to slide ids, then
   `work.py open --dir D --scope s-7f3a,s-91c0 [--add N] [--remove id] --note "…"`.
   This also saves the person's own hand edits as a `-user` version, so your work starts
   from their file, not from your memory of it.
2. **Edit only that scope.** Read the files fresh from disk. Do not touch other slides, not
   even to "tidy" them. If the same problem is on other slides ("check the other slides
   for the same issue"), put those slides in the scope too.
3. **Cut.** `work.py cut --dir D`. If the guard fails, you changed something outside the
   scope: undo that change. Do not widen the scope to make the guard pass, unless the
   person asked for that change.

## 5. The guard (why the person's work is never lost)

On `open`, hand edits are saved first. On `cut`, each slide outside the scope is compared
with the base version. A slide that was **modified, deleted or moved** is a violation, and
the message names the slide, the text block or the image. Deleting a slide in the scope is
fine (declare it with `--remove`). A change to a `_shared` file (shared.css, assets/) is a
violation unless the open `--note` contains the word `shared`. In a PPTX export, the
template slides (cover, base slides with a footer) also count as outside the scope.

The guard answers the complaints that made it: "you reverted my change", "you deleted my
two slides", "the portraits are gone".

## 6. Craft checks and review

`cut` measures the rendered pages and runs the craft checks. Each check came from a real
complaint:

| Check | What it measures | The complaint |
|---|---|---|
| `font_floor` | the smallest body text is at least a ratio of the canvas size | "the text is too small" |
| `fill` | how much of the safe area the content covers; too little is "too empty" | "too empty", "cramped" |
| `align` | left and top edges of sibling blocks line up | "not aligned" |
| `nowrap` | a title or subtitle marked one-line renders as one line, on every slide | "the subtitle must not wrap; check the other slides too" |
| `density` | words per slide stay under a limit for the canvas | "too much text" |
| `overflow`, `overlap`, `safe_area` | text leaves its box, elements collide, content leaves the safe area | earlier poster checks |
| `reference` | distance from a reference deck's type scale, margins and colors | "match the style of our earlier deck" |

Mark one-line titles with `data-nowrap` (or `white-space: nowrap`), and set `data-role`
(title, subtitle, body, label, caption) where the guess from tags and classes would be wrong.
Fix a failed check at its cause, on every slide with the same cause, in one pass.

**Review: at most 2 rounds after each version.** Round 1 looks for defects. Round 2 is an
optional craft round: at most 3 changes on at most 3 slides. Then stop and deliver what is
left as "still open".

Reviewers are the content experts in the product's expert catalog (see s_expert-review's
CATALOG.md). Spawn only the ones the version needs, in parallel, with the Agent tool,
`subagent_type: "pipeline-expert"`, and a prompt whose first line is `EXPERT-ID: <id>`:

| Expert | Use it when | Give it |
|---|---|---|
| `content-accuracy` | the content states facts, numbers, names or claims | the source text, the sources and project knowledge it may look up; an approximate number must not become an exact one |
| `content-visual` | anything was rendered | `review/` contact sheet and frames, `measure.json`, the kind page; it judges against s_frontend-design's design-judgment and the checks above |
| `content-copy` | there is text for an audience or a platform | the copy, the audience, the platforms |

Name the expert's prompt file by the path in the catalog. Treat each finding as a lead:
check it against the work, group the real ones by cause, fix them in one pass, and cut again.
A reviewer never blocks delivery; only `cut` failing does. If a high finding is still open
after round 2, deliver and say so plainly.

## 7. Delivery

Full template: cards/deliver.md. In short:

1. **Canvas opens the current version** (`ui_action` command `open-canvas-file` with the
   workspace-relative path of `out/deck.html`, `out/poster.png` or `out/page.png`). For a
   work outside the workspace, or with no Canvas, open it in the browser or give the path.
2. **One copy-ready chat block:** one line per output (file, version, size); each
   platform's copy text, ready to paste; thumbnails inline as images.
3. **One paragraph:** the thesis; what you decided for the person; what review found and
   what you fixed; what is left; which slides this version changed (by number and id).

Never publish, post or send. Publishing is always the person's click.

## 8. Rules that stay

- **The confirmed formats are the scope.** Make what the person asked for, and only that.
  You may suggest one more format in the delivery paragraph; you do not make it unless
  they say yes. Only the person removes or adds a format.
- **Ask at most once.** At the start, one message with only the questions whose answer you
  cannot decide (cards/brief.md). Everything else you decide and disclose.
- **Brand is data.** The default brand is neutral: a plain style and no logo.
  `"$SWARM_BACKEND_BIN" py "$SKILL_DIR/scripts/brand_config.py"` prints the effective brand
  (the shipped `brand/identity.yaml` with the user's `~/.swarm-ai/pollinate-brand.yaml` on
  top; `POLLINATE_BRAND_FILE` points at another file). `--signature` prints only the
  signature: watermark, link, link text, QR images (`dark_on_light`, `light_on_dark`) and
  footer. Every visual output carries each configured element; an unset element is not
  drawn. Use the configured colors and fonts exactly.
- **Content principles** (`brand/content_principles.md`) apply to anything public: value,
  not output counts (no lines of code, commits or "N days" as the claim); what is true, not
  "what I built" (`p2_scan.py` finds hero framing); one thesis; effects over mechanisms;
  English only where it is stronger than Chinese; each piece stands alone; no internal
  terms in the body.
- **No AI openers.** `geo_score.py <file.md> --json` flags generic AI openers ("In today's
  rapidly evolving…") and unsupported superlatives in English text. For Chinese text, read
  the opening line yourself against the same idea: no empty scene-setting, start with the
  point.
- **Platform defaults follow the person's language.** English: X and LinkedIn. Chinese:
  小红书, 朋友圈 and 公众号. A platform the person names wins.
- **Every check is a script, not your opinion.** If you think a check is wrong for this
  work, say so in the delivery paragraph; do not edit the check or skip `cut`.

## 9. Files

| Path | What it is |
|---|---|
| `cards/brief.md`, `cut.md`, `revise.md`, `deliver.md` | the four steps, short |
| `kinds/deck.md`, `poster.md`, `page.md`, `copy.md` | how to make each kind |
| `templates/html-deck/` | 34 deck design systems (`systems/<id>/design.md`) and the shared deck stage (`shared/`) |
| `templates/html-report/` | report, scorecard and comparison page templates |
| `brand/` | identity, poster design system, directions D1–D5, content principles, tone guide |
| `references/platform-adaptive.md`, `platform-specs.md` | sizes and limits per platform |
