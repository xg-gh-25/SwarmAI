# Pollinate -- Personal Content Delivery Engine

> One message → all professional formats → all audiences → quality guaranteed.
> **搞清楚再动手。** DISCOVER first, produce only what's confirmed.

Drive the full content lifecycle from topic to published deliverables. You ARE
the orchestrator -- execute each stage's behavior inline within this session,
don't invoke separate skills.

## Core Loop

For every pipeline run, follow this loop:

```
0. DISCOVER -- ask what user needs: who, what outcome, what context, how many formats
              → user confirms scope → only confirmed tracks proceed
1. INIT     -- parse topic, detect domain, load or create pipeline run
2. STAGE    -- for each stage in the pipeline:
               a. Gate check (budget, retries, escalations)
               b. Load context (Knowledge/ + upstream outputs)
               c. Execute stage behavior
               d. Classify decisions (mechanical/taste/judgment)
               e. Verify output (checklist + files exist)
               f. Handle result (advance / retry / checkpoint)
3. DELIVER  -- at delivery stage, run the Delivery Gate
4. COMPLETE -- summarize, reflect, record metrics
```

---

## Step 0: DISCOVER — "搞清楚再动手"

> The highest-ROI activity is clarifying what to produce.
> One good discovery saves 4 wasted tracks.

DISCOVER happens BEFORE everything else. It is the ONLY mandatory human interaction
point — all other stages can auto-proceed with taste decisions batched at delivery.

### Fast Path Detection (check FIRST)

Before asking any questions, scan the user's trigger message for explicit format mentions:

```bash
python "$SKILL_DIR/scripts/format_recommend.py" --message "{user_message}" --json
```

If `mode: "fast_path"` → formats detected in message. Confirm and skip questions:

```
Got it. {detected_formats}. Proceeding with:
  {track list}

(Say "加 X" to add more, or "只做 Y" to narrow.)
```

→ Save discovery.json with fast-path results → Skip to INIT.

**Fast-path discovery.json** (audiences/outcomes/contexts are inferred or set to "unspecified"):
```json
{
  "message": "{extracted topic from user message}",
  "audiences": ["inferred"],
  "outcomes": ["inferred"],
  "contexts": ["inferred"],
  "scope": "explicit",
  "confirmed_tracks": ["{detected formats}"],
  "deferred_tracks": [],
  "rationale": "Fast-path: user explicitly named formats.",
  "fast_path": true,
  "created_at": "{ISO timestamp}"
}
```
Note: `"inferred"` values are acceptable for fast-path. EVALUATE and THINK stages
still work — they use `confirmed_tracks` for scope, not audiences/outcomes/contexts.

### Full Discovery (when fast-path not detected)

Ask the user these questions. You may ask them conversationally (not as a rigid form)
and extract answers from natural language. The goal is clarity, not interrogation.

**Q1: MESSAGE** — "你想说什么？一句话 thesis。"
  Extract: the core claim / insight / message to communicate.

**Q2: AUDIENCE** — "谁需要看到这个？"
  Canonical values: `leadership`, `customer`, `team`, `developer_community`, `social_followers`
  (User may say "给 Rob 看" → leadership. "发到社区" → developer_community.)

**Q3: OUTCOME** — "他们看完后你希望发生什么？"
  Canonical values: `awareness`, `alignment`, `action`, `data_decision`, `education`
  (User may say "让他同意" → alignment. "让人知道这件事" → awareness.)

**Q4: CONTEXT** — "他们在什么场景看到？"
  Canonical values: `meeting`, `email`, `social_media`, `search_learn`, `commute`, `analysis`
  (User may say "周四开会用" → meeting. "发朋友圈" → social_media.)

**Q5: SCOPE** — "现在需要多少？"
  Canonical values: `single`, `focused` (2-3), `full` (all matching), `recommend`
  (User may say "先出最核心的" → single. "全套" → full.)

### Generate Recommendation

After collecting answers, run the recommendation engine:

```bash
python "$SKILL_DIR/scripts/format_recommend.py" \
  --audiences {audience1} {audience2} \
  --outcomes {outcome1} {outcome2} \
  --contexts {context1} {context2} \
  --scope {scope} \
  --json
```

### Present Recommendation (BLOCKING — wait for confirmation)

```
Based on your answers:
  Audience: {audience_labels}
  Outcome: {outcome_labels}
  Context: {context_labels}

Recommended formats:
  P0: {track} — {rationale}
  P1: {track} — {rationale}
  [P2: {track} — {rationale}]

  [Deferred: {track} — can add later from same content_package]

Confirm? Or adjust:
  - "只做 {P0}" → narrow to P0 only
  - "加 {format}" → add a track
  - "全做" → include deferred tracks too
```

**BLOCKING:** Do NOT proceed to INIT until user confirms. A simple "ok", "好", "go",
"proceed", "确认" is sufficient confirmation.

### Save discovery.json

After confirmation, save to `content/{name}/discovery.json`:

```json
{
  "message": "{user's thesis}",
  "audiences": ["{canonical values}"],
  "outcomes": ["{canonical values}"],
  "contexts": ["{canonical values}"],
  "scope": "{single|focused|full|recommend}",
  "confirmed_tracks": ["{track names user confirmed}"],
  "deferred_tracks": ["{tracks that matched but user deferred}"],
  "rationale": "{why these tracks for these audiences}",
  "fast_path": false,
  "created_at": "{ISO timestamp}"
}
```

### Incremental Resume (adding tracks to existing content)

When user says "再出个 narrative" or "加个 deck" for an EXISTING content directory:

1. Find existing `content/{name}/discovery.json` and `content_package.md`
2. Add the new track to `confirmed_tracks`
3. **Skip EVALUATE and THINK** (upstream work already done)
4. Jump directly to PLAN for the new track only
5. Execute BUILD → REVIEW → DELIVER for the new track

This is the key benefit of layered architecture: content_package persists, new tracks
are additive, upstream work is never repeated.

### Downstream Impact of DISCOVER

| Stage | What changes |
|-------|-------------|
| INIT | Creates directories only for `confirmed_tracks` (not all 11) |
| EVALUATE | Evaluates ROI against confirmed scope (poster-only needs less readiness than full-suite) |
| THINK | Research depth calibrated: single-track = lighter, full = deeper competitive analysis |
| STRATEGIZE | `production_tracks` in strategy.json MUST equal `confirmed_tracks` from discovery.json. STRATEGIZE cannot add or remove tracks without user re-confirmation. If channel_matrix analysis suggests a track NOT in confirmed_tracks, log it as "suggested but not confirmed" — do NOT silently add it. |
| PLAN | Only populates content_package layers needed by confirmed_tracks |
| BUILD | Only executes confirmed_tracks — zero wasted production |
| REFLECT | Records what user chose (demand signal for future recommendations) |

### Authority Rule (CRITICAL)

**discovery.json `confirmed_tracks` is the single source of truth for scope.**
No downstream stage can override it. STRATEGIZE, EVALUATE, and PLAN are advisory —
they can WARN ("asset readiness is low for this track") but cannot REMOVE a track
the user confirmed. Only the user can change scope (via explicit message like "去掉
video" or "只做 poster").

---

## Step 1: INIT

### Starting a New Pipeline

**Prerequisites:** DISCOVER stage completed. `discovery.json` exists with `confirmed_tracks`.

**Backward compatibility:** If discovery.json does NOT exist (user triggered pipeline
without going through DISCOVER — e.g., legacy "make content about X" pattern):
1. Run fast-path detection on the user's message
2. If formats detected → auto-generate discovery.json with `fast_path: true`
3. If NO formats detected → trigger DISCOVER (ask the 5 questions). Do NOT crash.
4. Never block INIT solely on file absence — trigger the prerequisite stage if missing.

Parse from discovery.json + user's message:
- **Topic:** the thesis/message from discovery
- **Domain:** which knowledge area (AIDLC, AI Architecture, Industry Insights, etc.)
- **Confirmed Tracks:** from discovery.json `confirmed_tracks` array
- **Platforms:** inferred from tracks (poster → XHS/朋友圈, video → B站/YouTube, deck → N/A)

Create the content directory under `Knowledge/Pollinate/` (visible in Explorer,
git-tracked, part of the knowledge system — NOT Services/ which is hidden).
**CRITICAL: Always prefix with `YYYY-MM-DD-` for discoverability and maintenance.**

**Only create directories for confirmed tracks:**
```bash
SKILL_DIR="$(cd "$(dirname "$0")" && pwd)"
TODAY=$(date +%Y-%m-%d)
CONTENT_DIR="$HOME/.swarm-ai/SwarmWS/Knowledge/Pollinate/${TODAY}-{name}"
mkdir -p "$CONTENT_DIR/deliver"

# Create track directories ONLY for confirmed tracks from discovery.json
# Examples — only include tracks that appear in confirmed_tracks:
# mkdir -p "$CONTENT_DIR/tracks/video"      # if "video" in confirmed_tracks
# mkdir -p "$CONTENT_DIR/tracks/narrative"   # if "narrative" in confirmed_tracks
# mkdir -p "$CONTENT_DIR/tracks/poster"      # if "poster" in confirmed_tracks
# mkdir -p "$CONTENT_DIR/tracks/shorts"      # if "shorts" in confirmed_tracks
# mkdir -p "$CONTENT_DIR/tracks/deck"        # if "deck" in confirmed_tracks
# mkdir -p "$CONTENT_DIR/tracks/html-deck"   # if "html_deck" in confirmed_tracks
# mkdir -p "$CONTENT_DIR/tracks/pdf"         # if "one_pager" or "full_pdf" in confirmed_tracks
# mkdir -p "$CONTENT_DIR/tracks/data-report" # if "data_report" in confirmed_tracks
# mkdir -p "$CONTENT_DIR/tracks/document"    # if "document" in confirmed_tracks
```

Create `content/{name}/run.json`:
```json
{
  "id": "run_p_{8-char-uuid}",
  "type": "pollinate",
  "topic": "...",
  "domain": "...",
  "confirmed_tracks": ["poster", "deck"],
  "platforms": ["xiaohongshu", "pengyouquan"],
  "status": "running",
  "stages": [],
  "taste_decisions": [],
  "discovery_ref": "discovery.json",
  "created_at": "<ISO timestamp>",
  "updated_at": "<ISO timestamp>"
}
```

Announce:
```
Pollinate started: "{topic}" (run_p_{id})
Domain: {domain}
Tracks: {confirmed_tracks list}
Platforms: {relevant platforms}
```

### Resuming a Pipeline

When the user says "resume pollinate" or drags a Radar todo:

1. Read `content/{name}/run.json`
2. Check pending escalations -- if any still open, report and wait
3. Skip completed stages, resume from the checkpoint stage
4. Announce:
```
Pollinate RESUMED: "{topic}" (run_p_{id})
Completed: evaluate, think, plan
Resuming from: build
```

### Adding a Track to Existing Content (Incremental)

When user says "再出个 narrative" or "加个 deck" for existing content:

1. Find existing `content/{name}/` directory
2. Read `discovery.json` — add new track to `confirmed_tracks`
3. Read `content_package.md` — already populated from prior run
4. Create the new track directory: `mkdir -p "$CONTENT_DIR/tracks/{new_track}"`
5. **Skip EVALUATE and THINK** — upstream work persists
6. Jump to PLAN for the new track, then BUILD → REVIEW → DELIVER
7. Announce:
```
Pollinate INCREMENT: adding "{track}" to "{topic}" (run_p_{id})
Reusing: content_package.md, research.md (from prior run)
Building: {new_track} only
```

---

## Execution Modes

| Mode | Trigger | Behavior |
|------|---------|----------|
| **Auto** | Default / "make content about..." | Full pipeline with defaults, mandatory stop at Studio preview |
| **Interactive** | "interactive" / "I want to control each step" | Prompts at every decision point |
| **Resume** | "resume pollinate" / drag Radar todo | Load checkpoint, skip completed stages |

---

## Decision Classification

Every non-trivial decision during stage execution MUST be classified:

| Classification | Definition | Action | Content Example |
|---|---|---|---|
| **Mechanical** | One correct answer, deterministic | Auto-approve | "ROI = 4.2, threshold is 3.0 -> GO" |
| **Taste** | Reasonable default, human might differ | Accumulate for delivery gate | "Dark theme for AI architecture topic" |
| **Judgment** | Genuinely ambiguous, needs human | Block, checkpoint | "Should we include controversial claim X?" |

**Content-specific decision examples:**

| Stage | Decision | Classification | Default |
|-------|----------|---------------|---------|
| EVALUATE | ROI calculation | Mechanical | Formula output |
| EVALUATE | Format recommendation | Mechanical | From lookup table |
| THINK | Differentiation angle | Taste | Agent's best analysis |
| PLAN | Content Package structure | Taste | 5-7 key points |
| PLAN | Component selection per section | Taste | From content-type table |
| PLAN | Script length adjustment | Mechanical | Against duration target |
| BUILD | TTS backend selection | Mechanical | user_prefs > env > edge |
| BUILD | Speech rate adjustment | Taste | +0% default |
| BUILD | Color theme override | Taste | From domain_themes |
| REVIEW | Fix RP-V finding | Mechanical | Must fix all failures |
| DELIVER | Metadata tone/style | Taste | Per-platform defaults |
| REFLECT | Which patterns to record | Mechanical | Record all observations |

Log each decision in the pipeline run state:
```json
{
  "stage": "plan",
  "description": "Used dark theme for AI architecture topic",
  "classification": "taste",
  "reasoning": "domain_themes.ai_architecture.theme=dark in identity.yaml, matches technical depth"
}
```

---

## Checkpoint Protocol

### When to Checkpoint

Checkpoint (pause the pipeline) when ANY of:
- Judgment decision (e.g., "Should we include this controversial opinion?")
- Stage retry exhaustion (>= max_retries failures)
- Context budget >60% consumed
- Taste decision unresolvable (agent cannot pick a reasonable default)
- User absent (no response after judgment escalation)

### How to Checkpoint

1. Save pipeline state to `content/{name}/run.json` with status "paused"
2. Present to user:
```
Pollinate PAUSED at {STAGE} (run_p_{id})
Reason: {why}

  Completed: evaluate, think, plan
  Next: build
  Pending: {escalation summary}

  Resume: resolve the issue, then "resume pollinate for {topic}"
```

---

## Structured Chat Output

### Quality First Rule

Output formatting MUST NOT degrade pipeline execution quality.
- Output is generated AFTER stage execution completes, not during
- Output is NEVER a retry trigger (gates = the only quality mechanism)
- If token budget is tight, compress to status-only lines
- Agent priority: execute → verify → THEN format output

### Pipeline Briefing (shown once at start)

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Pollinate: "{topic}" (run_p_{id})
Domain: {domain} | Formats: {poster/video/narrative/shorts}
Platforms: {list}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Message first, format follows.

  How: PR/FAQ distills core message
       Channel matrix selects audience-format fit
       Brand conformance gates every output

  Quality gates:
       ★ Content Principles — P1-P8 anti-pattern scan (external content)
       ★ Brand Conformance — identity.yaml exact match
       ★ Platform Specs — per-platform validation

  Stages: EVALUATE → THINK → STRATEGIZE → PLAN → BUILD → REVIEW → DELIVER → REFLECT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### Progress Display (per-stage, 1-3 lines each)

```
## ✦ EVALUATE [Topic-Value Gate]
→ <GO/DEFER/REJECT> | ROI <X.X> | Format: <formats> | Assets: <N> files

## ✦ THINK [Research + Differentiation]
→ Thesis: "<one sentence>" | Differentiation: "<angle>"
  Competitive: <N> sources reviewed | Internal: <N> assets mapped

## ✦ STRATEGIZE [PR/FAQ + Channel Matrix]
→ <N> channels × <M> formats | Message: "<core message>"
  Production tracks: <poster/video/narrative/shorts>

## ✦ PLAN [Content Package + Per-Track Specs]
→ <N> key points | <M> sections | Duration: <est>
  Components: <list for video> | Spec: <poster dimensions/style>

## ✦ BUILD [Production]
→ <track>: <status> | Files: <N> produced
  <per-track one-liner: "TTS 6:42 @ Zhiyu" or "Poster 1080×1080 rendered">

## ✦ REVIEW [Quality Scan]
→ RP-V: <N>/<total> pass | Brand: <✓/✗> | Content Principles: <✓/✗>
  Findings: <N> fixed, <M> warnings

## ✦ DELIVER [Structured Delivery]
  ├─ Taste Gate: <N> decisions → <approved/overridden/none>
  ├─ Confidence: <score>/10
  ├─ Platforms: <list with status>
  └─ Deliverable block (see below)

## ✦ REFLECT [Learn + Improve]
→ <N> lessons → IMPROVEMENT.md | Prefs updated: <Y/N>
```

### Deliverable Block (DELIVER stage output — the user's final package)

This is what the user takes away. Every DELIVER stage MUST output this block
in the chat window. The user should be able to copy-paste directly to publish.

**Format: Poster + Text deliverables (2-variant user-facing output):**

The delivery output is designed for the USER, not for the developer. The user wants:
1. See the posters immediately (hero content)
2. Pick one with zero friction
3. Get publish-ready copy text
4. Know quality was verified (trust signal, not noise)

**CRITICAL RULES:**
- **Platform Matrix is the delivery.** User sees: each platform → its asset + its copy. Like a publishing dashboard.
- **Quality gates are invisible when passing.** One-line trust signal, not a table.
- **Copy is naked and ready to paste.** Zero preamble, zero markdown, zero instructions mixed in.
- **Images shown inline.** Read tool on .png. User SEES the poster — no file paths.
- **User picks direction FIRST, then gets the full platform matrix for that direction.**

**Structure: Direction Selection → Platform Matrix**

The output has two phases:
1. **Phase 1: Pick Direction** — show both posters, user says "发 A" or "发 B"
2. **Phase 2: Platform Matrix** — for the chosen direction, output the full publish-ready package per platform

---

**Phase 1 Template (Direction Selection):**

```
🐝 **{topic}** — 两个方向，选一个发

**A. {Chinese Name}** — {mood}

[INLINE IMAGE A]

**B. {Chinese Name}** — {mood}

[INLINE IMAGE B]

✅ 质量通过 (8/8) · "发 A" 或 "发 B"
```

Rules:
- Images inline (Read tool on .png)
- ONE line for quality + CTA combined
- Nothing else. No file paths, no gate tables, no numbered menus.
- Wait for user to pick.

---

**Phase 2 Template (Platform Matrix — after user picks):**

```
🐝 **{topic}** — {chosen direction} · 发布就绪

---

### 📱 小红书

**素材:** [INLINE IMAGE — 1080×1440 cropped if needed, or full long-form]

**标题:**
{title — ≤20 chars, punchy, with emoji}

**正文:**
{body text — XHS style, short paragraphs, emoji-friendly}

**标签:**
{#tag1 #tag2 #tag3 #tag4 #tag5}

---

### 💬 朋友圈

**素材:** [SAME IMAGE — or square crop 1080×1080 if needed]

**文案:**
{complete text — one block, copy-paste directly to WeChat Moments}

---

### 🐦 Twitter / X

**素材:** [1280×720 crop or OG version if available]

**Tweet:**
{English or bilingual, ≤280 chars, observation + opinion format}

---

### ✨ 你还可以

- "出 Story 版本" — 竖屏 9:16
- "调整文案语气" — 更正式/更轻松
- "再出一个主题" — 下一张海报
```

---

**Display Rules (binding — agent MUST follow):**

1. **Phase 1 is MINIMAL.** Two images + one line. Nothing else. User doesn't need context — they need to see and pick.

2. **Phase 2 is COMPLETE.** Every platform gets: asset + ALL copy fields. User reviews top-to-bottom, copies per platform. No jumping between sections.

3. **NEVER show raw file paths.** Images shown inline. If the user needs to download, they right-click the inline image.

4. **NEVER show L1-L8 table unless a gate FAILED.** When all pass: `✅ 质量通过 (8/8)` — 6 words, done.

5. **Platform sections are self-contained.** User can mentally "close" a platform after copying from it. No cross-references between platforms.

6. **Copy text is NAKED per platform.** Each platform's copy is styled for THAT platform:
   - 小红书: short title + paragraphs + hashtags (separate fields)
   - 朋友圈: one text block (no title, no hashtags — WeChat doesn't have them)
   - Twitter: single tweet (≤280 chars, English-friendly)

7. **Asset per platform may differ.** Same content, but:
   - 小红书: full long-form or 3:4 crop
   - 朋友圈: same or 1:1 crop
   - Twitter: 16:9 crop or OG card
   If only one version was rendered (long-form), use it for all — note "长图" for XHS, "首图" for 朋友圈.

8. **"你还可以" is max 3 items.** Contextual to THIS output. Not generic suggestions.

9. **If gate FAILED during convergence:** user should almost never see this. If they do, show ONE line:
   ```
   ⚠️ 修复中: {specific issue} → 重新生成...
   ```
   Then fix silently and present the clean output. Don't show the failure as a "finding" — fix it.

**Format: Video deliverables:**

```
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ 📦 POLLINATE DELIVERY — run_p_{id}     ┃
┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛

Topic: {topic}
Format: Video ({duration}) | Confidence: {score}/10
Platforms: {list}

── VIDEO ───────────────────────────────

Preview: {path or "open in Studio"}
Duration: {mm:ss} | Resolution: {WxH}
TTS: {backend}/{voice} | BGM: {track}

── THUMBNAILS ──────────────────────────

{inline thumbnail image if small enough}
16:9: {path}  |  4:3: {path}  |  3:4: {path}

── PLATFORM METADATA ───────────────────

B站: {title}
     Tags: {tags}
YouTube: {title}
         Tags: {tags}
小红书: {title}
        Tags: {tags}

── FILES ───────────────────────────────

video:     {path}
script:    {path}
thumbnails: {paths}
report:    {path}

── TASTE DECISIONS (if any) ────────────

{numbered list}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**Format: Multi-deliverable (poster series, campaign):**

```
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ 📦 POLLINATE DELIVERY — run_p_{id}     ┃
┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛

Topic: {topic}
Format: {N} pieces × {formats} | Confidence: {score}/10
Platforms: {list}

── SERIES OVERVIEW ─────────────────────

| # | Title/Hook | Thesis | Status |
|---|-----------|--------|--------|
| 1 | ...       | T3     | ✅ ready |
| 2 | ...       | T1     | ✅ ready |
| ...

── PIECE #{N} ──────────────────────────

[inline poster image]

朋友圈:
{copy text}

小红书:
{title + body + hashtags}

── FILES ───────────────────────────────

{file manifest per piece}

── PUBLISHING PLAN ─────────────────────

Recommended order: #{order}
Cadence: {interval recommendation}
First publish: #{which} — {reason}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**Format: Document tracks (deck/pdf/data-report/document/image/interactive-report/podcast):**

```
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ 📦 POLLINATE DELIVERY — {topic}        ┃
┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛

Tracks: {confirmed_tracks list} | Direction: {D#} {name}
Thesis: "{one sentence from content_package}"

── TRACK E: DECK ──────────────────────

📊 {topic}.pptx — {N} slides, speaker notes ✓
   Audience: {from discovery.json}
   Key slides: {slide 1 title} → {slide N title}
   
   File: content/{name}/tracks/deck/{topic}.pptx

── TRACK F: PDF ───────────────────────

📄 {topic}-onepager.pdf — 1 page, scannable
   Sections: {header} / {proof points} / {CTA}
   
   [INLINE: first page preview PNG if available]
   
   File: content/{name}/tracks/pdf/{topic}-onepager.pdf

── TRACK G: DATA REPORT ───────────────

📈 {topic}.xlsx — {N} sheets, {M} charts
   Sheets: {Overview} | {Detail} | {Comparison}
   Key metric: {hero number with context}
   
   File: content/{name}/tracks/data-report/{topic}.xlsx

── TRACK H: DOCUMENT ──────────────────

📝 {topic}.docx — {N} pages, TOC ✓
   Structure: Executive Summary → {sections} → Appendix
   
   File: content/{name}/tracks/document/{topic}.docx

── TRACK I: AI IMAGE ──────────────────

🎨 {topic}-hero.png — {WxH}, {style}
   Purpose: {deck illustration / article hero / social thumbnail}
   
   [INLINE IMAGE if generated]
   
   Prompt: content/{name}/tracks/image/prompt.json
   File: content/{name}/tracks/image/{topic}-hero.png

── TRACK J: INTERACTIVE REPORT ────────

📊 {topic}-report.html — {mode: dashboard/scorecard/comparison}
   Sections: {tab1} | {tab2} | {tab3}
   Interactive: tabs ✓, expand ✓, traffic lights ✓
   
   Open: content/{name}/tracks/interactive-report/{topic}-report.html

── TRACK K: PODCAST ───────────────────

🎙️ {topic}-podcast.mp3 — {duration}, {language}
   Hosts: {Host A} & {Host B}
   Key points: {3-5 bullet summary of dialogue}
   
   Audio: content/{name}/tracks/podcast/{topic}-podcast.mp3
   Transcript: content/{name}/tracks/podcast/transcript.md
   Show notes: content/{name}/tracks/podcast/show_notes.md

── QUALITY ─────────────────────────────

✅ Per-track RP: {E: 8/8} | {F: 7/7} | {G: 6/6} | ...
✅ Cross-format (RP-X): {5/5 pass}
Direction: {D# name} applied consistently

── FILES (all outputs) ────────────────

{one line per produced file, grouped by track}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**Display rules for document tracks:**

1. **Only show sections for confirmed tracks.** If run only produced deck + pdf, omit tracks G/H/I/J/K sections entirely.

2. **Inline images where possible.** Track I hero image, Track F preview PNG — show them inline via Read tool. User SEES the result.

3. **File paths are actionable.** Each track section ends with the output file path — user can open directly.

4. **Quality is one section, not per-track noise.** Aggregate RP pass rates into a single trust line. Only expand if something FAILED.

5. **Podcast shows key points.** User wants to know what the hosts discuss without listening to the whole thing. 3-5 bullet summary from show_notes.

6. **Interactive report gets "Open:" not "File:"** — because the user should open it in a browser, not download it.

7. **Multi-track runs use this template.** Even if one of the tracks is poster/video — those get their Platform Matrix treatment ABOVE this block, and document tracks get this treatment BELOW.

---

### Completion Summary (shown once at end, after REFLECT)

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✦ COMPLETE | READY TO PUBLISH
  Confidence: {score}/10 | Platforms: {N} | Files: {N} produced
  Lessons: {N} → IMPROVEMENT.md | Report: {path}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### Structural Validator (BLOCKING — run before presenting deliverable)

Before outputting the deliverable block, run the structural validator:

```bash
python backend/skills/s_pollinate/scripts/pollinate_validator.py <content_dir> --json
```

**If `valid: false`:** Fix all errors before presenting to user. Re-run until valid.
**If `valid: true`:** Proceed to deliverable block.

The validator checks 6 invariants mechanically:
1. Platform matrix present (platform_matrix.md or section)
2. QR code image present (qr-*.png)
3. GitHub link in delivery text
4. 2+ variant files per track
5. Output files have valid extensions
6. Content directory structure valid

These are non-negotiable structural requirements — every delivery must pass regardless
of content type (poster, video, narrative). The validator is the Pollinate equivalent
of pipeline_validator.py.

### Display Rules

1. **Deliverable block is non-negotiable.** Every DELIVER stage outputs the full
   block in chat. User should never have to ask "where's my output?"

2. **Inline images when possible.** Poster PNGs should be shown inline (via Read
   tool on the .png file). If too large, show path + thumbnail description.

3. **Copy-paste ready.** Text in the deliverable block must be EXACTLY what the
   user pastes to the platform. No markdown formatting that breaks on paste. No
   instruction text mixed with content.

4. **Separate content from metadata.** Platform-specific formatting (hashtags,
   title rules) goes in the platform section, not mixed into the copy block.

5. **Progress display is lightweight.** Don't repeat full deliverable content in
   progress lines. Progress = status. Deliverable block = content.

6. **Series deliverables show all pieces.** Don't make the user ask for each one.
   If it's a 6-poster series, the deliverable block lists all 6 (with inline
   images for completed ones).

### Stage Status Indicators

- `[done]` = completed successfully
- `[>>>>]` = currently executing
- `[skip]` = skipped (not applicable for this run)
- `[FAIL]` = failed, will retry or checkpoint
- `[STOP]` = checkpointed (pipeline paused)
- `[    ]` = pending

---

## Max Retries Per Stage

| Stage | Max Retries |
|-------|-------------|
| EVALUATE | 2 |
| THINK | 2 |
| STRATEGIZE | 2 |
| PLAN | 2 |
| BUILD | 3 |
| REVIEW | 2 |
| TEST | 3 |
| DELIVER | 1 |
| REFLECT | 1 |

After exhaustion -> checkpoint with all failure details.

---

## Stage 1: EVALUATE -- Is this topic worth producing?

**Context from DISCOVER:** Read `discovery.json` for `confirmed_tracks` and `audiences`.
The ROI evaluation is scoped to the confirmed formats — a poster-only run needs less
asset readiness than a full 5-track run.

### Procedure

1. **Parse topic intent:** what claim, for whom, why now?

2. **Scan internal knowledge:**
   ```bash
   grep -rl "{keywords}" ~/.swarm-ai/SwarmWS/Knowledge/ ~/.swarm-ai/SwarmWS/.context/MEMORY.md
   ```
   List available assets: diagrams, data, code, quotes, prior analysis.

3. **Run evaluation script:**
   ```bash
   python "$SKILL_DIR/scripts/evaluate_topic.py" "{topic}" \
     --domain "{domain}" \
     --diff N --audience N --readiness N --timeliness N --complexity N \
     --json
   ```
   Where N is the agent's score for each dimension (0-5).

4. **Score on 5 dimensions (each 0-5):**

   | Dimension | Weight | Question | Scoring Guide |
   |-----------|--------|----------|---------------|
   | Knowledge Differentiation | 0.30 | Do we know something others don't? | 5=nobody else has this depth, 1=common knowledge |
   | Audience Match | 0.25 | Will AI practitioners/developers care? | 5=core audience, 1=irrelevant |
   | Asset Readiness | 0.20 | How much exists in Knowledge/ already? | 5=complete, 1=nothing |
   | Timeliness | 0.15 | Evergreen(1), trending(3), breaking(5) | Time sensitivity |
   | Production Complexity | 0.10 | Text-only(5) to custom 3D animation(1) | Execution difficulty |

5. **Calculate ROI:**
   ```
   ROI = (Differentiation * 0.30) + (Audience * 0.25) + (Readiness * 0.20)
       + (Timeliness * 0.15) + (Complexity * 0.10)
   ```

6. **Recommend:**
   - **GO** (>= 3.0) -- proceed with pipeline
   - **DEFER** (2.0-2.9) -- log reason, pipeline ends
   - **REJECT** (< 2.0) -- log reason, pipeline ends

7. **Validate against confirmed_tracks (from DISCOVER):**
   - Do the confirmed tracks make sense given the ROI scores?
   - If Asset Readiness is low (1-2) and confirmed_tracks include video → WARN user
     ("asset readiness is low for video — may require more research in THINK stage")
   - If topic is trivial (ROI < 3.0) but user explicitly confirmed → still GO
     (DISCOVER gives user final authority on scope; EVALUATE is advisory, not blocking,
     when user has explicitly confirmed in DISCOVER)
   - **EVALUATE can still REJECT** (ROI < 2.0) — this overrides DISCOVER. Bad topic = bad topic.

8. **Save** `content/{name}/evaluation.json`

9. **I1: Show topic backlog suggestions** (if backlog exists):
   ```bash
   python "$SKILL_DIR/scripts/topic_backlog.py" list --json
   ```
   Display the top 3 pending topics by ROI score as "alternatively, consider:"
   alongside the current evaluation result. This helps the user decide whether
   the current topic is the highest-value use of a pipeline run, or if a
   backlogged topic scores higher. The backlog is informational — never
   auto-switch topics.

If DEFER or REJECT -> pipeline ends. Log reason and exit.

### Decisions

| Decision | Classification | Default |
|----------|---------------|---------|
| ROI calculation | Mechanical | Formula output |
| Format recommendation | Mechanical | From lookup table |
| "Is this the right time to publish?" | Taste | Agent's timeliness assessment |
| "Should we cover this controversial topic?" | Judgment | Block, ask user |

### Verification Gate

Before advancing to THINK, ALL must be true:
- [ ] `content/{name}/evaluation.json` exists and is valid JSON
- [ ] ROI score is calculated with all 5 dimensions scored
- [ ] Recommendation is explicitly GO (not DEFER or REJECT)
- [ ] Internal knowledge scan completed (grep output reviewed)
- [ ] Format combination recommended
- [ ] Available internal assets listed

### Anti-Rationalization

| Shortcut | Required Response |
|----------|-------------------|
| "Topic is obviously good, skip scoring" | Every topic gets scored. Gut feel is not evaluation. |
| "Knowledge scan found nothing, but I know the topic" | Score Asset Readiness 0-1 honestly. Low readiness raises production risk. |
| "ROI is 2.8, close enough to GO" | 3.0 is the threshold. DEFER at 2.8. No rounding up. |
| "Skip format recommendation, just do video" | Recommend the right formats. Video-only is valid but must be a conscious choice. |

### Max Retries

2. After exhaustion -> checkpoint.

### Output Files

- `content/{name}/evaluation.json` -- topic scores, ROI, recommendation, format plan, asset inventory

---

## Stage 2: THINK -- Research + Differentiation

### Procedure

1. **Internal knowledge scan:**
   - Read identified Knowledge/ files from EVALUATE's asset inventory
   - Scan these directories:
     ```bash
     ls ~/.swarm-ai/SwarmWS/Knowledge/Notes/
     ls ~/.swarm-ai/SwarmWS/Knowledge/Designs/
     ls ~/.swarm-ai/SwarmWS/Knowledge/Reports/
     ```
   - Extract key data points, quotes, code examples, architecture decisions
   - Note connections to MEMORY.md entries (lessons, corrections, COEs)

2. **External competitive research** (use web search):
   - B站/YouTube: search top 5 videos on same topic
   - For each video, record in this template:

     | Field | Value |
     |-------|-------|
     | Title | ... |
     | Views | ... |
     | Duration | ... |
     | Structure | ... |
     | Top comments | ... |
     | Weaknesses | ... |

   - Articles: search 掘金/知乎/Medium for same topic
   - Identify: what's missing, wrong, or shallow

3. **Differentiation framing** -- answer all 3 questions:
   - "What do we know that others don't?"
   - "What did others get wrong or oversimplify?"
   - "What angle hasn't been covered?"

4. **Write** `content/{name}/research.md` with ALL required sections:
   - Core thesis (1 sentence)
   - Target audience profile
   - Differentiation angle
   - Internal asset manifest (file paths + excerpts)
   - Competitive content analysis (top 3-5)
   - Recommended narrative arc

### Decisions

| Decision | Classification | Default |
|----------|---------------|---------|
| Competitive research scope | Mechanical | Top 5 videos + top 3 articles |
| Differentiation angle | Taste | Agent's best analysis |
| Narrative arc recommendation | Taste | Based on content type |
| "Is competitor's approach better than ours?" | Judgment | Block if our angle is weaker |

### Verification Gate

Before advancing to STRATEGIZE, ALL must be true:
- [ ] `content/{name}/research.md` exists
- [ ] Core thesis is a single sentence (not a paragraph)
- [ ] Target audience profile is specific (not "everyone")
- [ ] Differentiation angle explicitly answers at least 1 of the 3 framing questions
- [ ] Internal asset manifest lists actual file paths (not placeholders)
- [ ] Competitive analysis covers >= 3 external sources with the template fields filled
- [ ] Recommended narrative arc is present

### Anti-Rationalization

| Shortcut | Required Response |
|----------|-------------------|
| "No competitors found, skip competitive analysis" | Search harder. Every technical topic has existing content somewhere. |
| "Internal assets are sufficient, skip external research" | External research validates differentiation. Internal-only content risks being redundant. |
| "Differentiation is obvious" | Write it down explicitly. Obvious to you is not obvious to the audience. |
| "Audience is developers, that's specific enough" | Which developers? Junior/senior? Backend/ML? What pain point? |

### Max Retries

2. After exhaustion -> checkpoint.

### Poster-Specific: Visual Reference Collection

If format includes poster, THINK **must** additionally:

5. **Collect visual references** (minimum 3):
   - Search 小红书/Pinterest/Dribbble for same topic + "poster" or "长图" or "card design"
   - For each reference, capture: layout pattern, typography scale, spacing rhythm, color approach
   - Write to `content/{name}/visual_references.md`

   This is NON-SKIPPABLE. "Poster is simple" is the #1 anti-rationalization for bad design.
   Visual design requires MORE research than script writing, not less.

| Shortcut | Required Response |
|----------|-------------------|
| "Poster is simple, skip visual research" | Visual output has higher quality variance than text. Research is mandatory. |
| "I know what good design looks like" | Show 3 references or admit you're guessing. |

### Output Files

- `content/{name}/research.md` -- thesis, audience, differentiation, assets, competitive analysis
- `content/{name}/visual_references.md` -- (poster format only) 3+ visual references with extracted patterns

---

## Stage 3: STRATEGIZE -- PR/FAQ + Channel × Format Matrix

### Procedure

#### Step 3a: Draft PR/FAQ

Every Pollinate run produces a PR/FAQ as the single source document — even for
a poster. All downstream formats extract from this PR/FAQ.

Create `content/{name}/PRFAQ.md`:

```markdown
# PRESS RELEASE

**Headline:** [one sentence — the value delivered]

**Problem:** [why this matters to the audience]

**Solution:** [what we produce and why it's different]

**Real Example:** [concrete, specific, verifiable proof]

**Quote:** [the "aha" sentence that captures the insight]

# FAQ

**Q: How is this different from X?**
A: ...

**Q: Who is this for?**
A: ...

**Q: What can I do with this?**
A: ...
```

The PR/FAQ must be concrete, not generic. "Real Example" requires actual data,
code, benchmarks, or specific names. No placeholder text like "demonstrates the
value" — show the actual value with numbers or quotes.

#### Step 3b: Channel × Format Decision Matrix

For each enabled channel (from `~/.swarm-ai/pollinate-accounts.yaml`), assess
audience fit and select optimal format.

**Decision rules (all mechanical unless controversial):**

| Condition | Format Decision | Classification |
|-----------|----------------|---------------|
| Breaking news + high timeliness | Poster-first (ship fast), video later if traction | Mechanical |
| Deep technical + high differentiation | Video + narrative (long-form depth) | Mechanical |
| Product launch | Full mix: poster + video + narrative + README | Mechanical |
| Audience fit < 3 for a channel | Skip channel | Mechanical |
| Controversial topic (sensitive claim) | Escalate for judgment | Judgment |

**Audience fit scoring (per channel):**
- 5 = Core audience, perfect match
- 4 = Strong fit, minor gaps
- 3 = Moderate fit, some friction
- 2 = Weak fit, low relevance
- 1 = No fit, wrong audience

Example assessment:
```
Channel Assessment:
┌─────────────┬─────────────┬────────────────────┬──────────┐
│ Channel     │ Audience Fit│ Best Format        │ Priority │
├─────────────┼─────────────┼────────────────────┼──────────┤
│ xiaohongshu │ 5           │ Poster + short text│ P0       │
│ bilibili    │ 5           │ Video + poster     │ P0       │
│ gongzhonghao│ 4           │ Narrative          │ P1       │
│ douyin      │ 3           │ Shorts (vertical)  │ P2       │
│ youtube     │ 2           │ Skip (EN audience) │ Skip     │
└─────────────┴─────────────┴────────────────────┴──────────┘
```

#### Step 3c: Write strategy.json

Save `content/{name}/strategy.json`:

```json
{
  "message": "...",
  "audience": "...",
  "desired_outcome": "awareness -> trial",
  "prfaq_path": "content/{name}/PRFAQ.md",
  "channel_matrix": [
    {"channel": "xiaohongshu", "format": ["poster", "short_text"], "priority": "P0", "audience_fit": 5},
    {"channel": "bilibili", "format": ["video", "poster"], "priority": "P0", "audience_fit": 5},
    {"channel": "gongzhonghao", "format": ["narrative"], "priority": "P1", "audience_fit": 4}
  ],
  "production_tracks": ["poster", "video", "narrative"]
}
```

The `production_tracks` array drives Stage 4 (PLAN) — one spec per track.

### Decisions

| Decision | Classification | Default |
|----------|---------------|---------|
| PR/FAQ structure | Mechanical | Standard template |
| Audience fit scoring | Mechanical | Formula per channel config |
| Format selection per channel | Mechanical | From decision rules table |
| Tone (professional/casual) | Taste | From domain in identity.yaml |
| "Should we cover this angle?" | Judgment | Block if controversial |

### Verification Gate

Before advancing to PLAN, ALL must be true:
- [ ] `content/{name}/PRFAQ.md` exists with all sections filled (no placeholders)
- [ ] PR/FAQ "Real Example" has concrete data (not "demonstrates value")
- [ ] `content/{name}/strategy.json` exists and is valid JSON
- [ ] Every enabled channel has an audience_fit score
- [ ] Channels with audience_fit < 3 are either skipped or have explicit override
- [ ] `production_tracks` array is populated with at least 1 track
- [ ] If controversial topic detected, escalation was handled

### Anti-Rationalization

| Shortcut | Required Response |
|----------|-------------------|
| "PR/FAQ feels redundant with research.md" | PR/FAQ is the source doc for all formats. Write it. |
| "Poster doesn't need a PR/FAQ" | Every format derives from PR/FAQ. Write one sentence if minimal. |
| "Channel selection is obvious" | Show the audience_fit scores. Obvious to you is not obvious to audit. |
| "Skip low-fit channels manually" | Let the mechanical rule skip them. Log the reason. |

### Max Retries

2. After exhaustion -> checkpoint.

### Output Files

- `content/{name}/PRFAQ.md` -- source document for all formats
- `content/{name}/strategy.json` -- channel matrix + production tracks

---

## Stage 4: PLAN -- Content Package + Per-Track Specs

### Procedure

Load `strategy.json` to determine which production tracks to plan. For each
track in `production_tracks`, generate the corresponding spec.

#### Step 4a: Content Package (format-agnostic core)

Create `content/{name}/content_package.md`:

```markdown
# {Title}

## Core Thesis
{One sentence -- the single idea this content exists to communicate}

## Key Points (5-7)
1. {Point} -- {Evidence/data}
2. ...

## Narrative Arc
- Hook: {10-15s -- question/contradiction/surprise}
- Setup: {Context the audience needs}
- Development: {Build the argument, section by section}
- Climax: {The "aha" moment / strongest evidence}
- Resolution: {What this means for the audience + CTA}

## Evidence Bank
- Data: {specific numbers, benchmarks, dates}
- Quotes: {expert opinions, source attribution}
- Code: {actual code snippets if applicable}
- Visuals: {diagrams, screenshots, existing assets}

## Internal References
- {file path}: {what to extract}
- ...
```

#### Step 4b: Video Script (if "video" in production_tracks)

Create `content/{name}/tracks/video/podcast.txt` with `[SECTION:xxx]` markers.

**Script structure:**
```
[SECTION:hero]
{Hook -- 10-15 seconds, grab attention}

[SECTION:setup]
{Context and problem framing -- 60-90s}

[SECTION:core_1]
{First key argument -- 60-90s}

[SECTION:core_2]
{Second key argument -- 60-90s}

...

[SECTION:climax]
{Strongest evidence / "aha" moment -- 60-90s}

[SECTION:outro]
{Summary + CTA -- 10-15s}
```

**Script rules:**
- Chinese: ~4 chars/second, concise paragraphs (50-80 chars)
- English: ~3 words/second
- `[SECTION:xxx]` markers are MANDATORY for timing.json generation
- Number formatting: digits OK with Chinese units; spell out dates, versions, long integers
- Platform-specific outro (B站 一键三连, YouTube subscribe, etc.)

#### Step 4c: Visual Composition Plan

For each section, select Remotion components:

| Content Type | Primary Component | Supporting Components |
|-------------|-------------------|----------------------|
| Architecture / flow | FlowChart | DiagramReveal |
| Code example | CodeBlock | -- |
| A vs B comparison | ComparisonCard | DataBar |
| Chronological story | Timeline | IconCard |
| Data / metrics | StatCounter, DataBar | DataTable |
| Expert opinion | QuoteBlock | -- |
| Feature list | FeatureGrid | IconCard |
| Concept introduction | IconCard | SectionLayouts |

**Visual composition rules:**
- No same component type in consecutive sections
- Content width >= 85% of screen
- Bottom 100px reserved for subtitles
- Hero title >= 84px, section title >= 72px, body >= 32px
- Apply domain theme from brand/identity.yaml `domain_themes`

#### Step 4d: Duration Dry-Run (if "video" in production_tracks)

```bash
python "$SKILL_DIR/scripts/generate_tts.py" \
  --input "content/{name}/tracks/video/podcast.txt" \
  --output-dir "content/{name}/tracks/video/" \
  --dry-run
```

Target durations:
- B站 horizontal: 3-8min (ideal), max 12min
- Shorts (小红书/抖音/视频号): 30-120s per section

If dry-run reports >12min -> revise script (trim sections).
If dry-run reports <3min -> revise script (add depth).

### Decisions

| Decision | Classification | Default |
|----------|---------------|---------|
| Content Package structure | Taste | 5-7 key points, standard arc |
| Component selection per section | Taste | From content-type table |
| Script length adjustment | Mechanical | Against duration target |
| Section count | Taste | 4-6 sections typical |
| Domain theme override | Taste | From identity.yaml domain_themes |

### Verification Gate

Before advancing to BUILD, ALL must be true:
- [ ] `content/{name}/content_package.md` exists with all template sections filled
- [ ] `content/{name}/video/podcast.txt` exists with `[SECTION:xxx]` markers
- [ ] Every section has a `[SECTION:xxx]` marker (no unmarked content)
- [ ] Dry-run duration is within target range (3-12min for B站)
- [ ] Visual composition plan maps every section to at least one component
- [ ] No same component type appears in consecutive sections
- [ ] Core thesis matches between content_package.md and research.md

### Anti-Rationalization

| Shortcut | Required Response |
|----------|-------------------|
| "Script is fine without section markers" | Markers drive timing.json. No markers = no audio-video sync. Always add them. |
| "Duration is 13 minutes, close enough" | 12:00 is max. Trim. Every extra minute loses viewers. |
| "Skip visual composition plan, I'll figure it out in BUILD" | Visual plan prevents BUILD rework. Plan every section now. |
| "Same component twice is fine for this content" | Variety keeps attention. Find a different component even if the content type is similar. |
| "Dry-run is slow, skip it" | 5 seconds of dry-run prevents 30 minutes of re-render. Always run it. |

### Max Retries

2. After exhaustion -> checkpoint.

### Output Files

- `content/{name}/content_package.md` -- core narrative, key points, evidence bank
- `content/{name}/video/podcast.txt` -- narration script with section markers
- `content/{name}/visual_plan.md` -- component mapping per section (if separate from content_package)
- `content/{name}/tracks/poster/spec.md` -- poster spec (if poster in production_tracks)

---

## Stage 5: BUILD -- Production

### Track Selection

BUILD executes per-track. Check `discovery.json → confirmed_tracks` (authoritative)
and run the applicable track(s) below.

**IMPORTANT:** `confirmed_tracks` from discovery.json is the SINGLE SOURCE OF TRUTH
for which tracks to build. Never build a track not in confirmed_tracks.

**NOTE:** Legacy tracks (A-D) may reference `production_tracks` from strategy.json.
These MUST equal `confirmed_tracks` — STRATEGIZE copies confirmed_tracks into
strategy.json.production_tracks for backward compatibility. If they ever diverge,
discovery.json wins.

### Direction Selection (applies to ALL brand-aware tracks)

Before building any track that uses brand colors (deck, PDF, data-report, document,
poster), determine the active design direction:

1. If poster was already built in this run → inherit its direction (consistency)
2. If no poster → score content against `brand/directions/d{N}-*.yaml` content_triggers:
   - strong match = 3, moderate = 2, weak = 1
   - Highest score = selected direction
3. Load the selected direction YAML for token extraction

**Default if no clear match:** D1 Obsidian (professional, technical — safest default)

The selected direction provides:
- Color tokens for CSS (Track F PDF), openpyxl charts (Track G), python-docx headings (Track H)
- PptxGenJS colors (Track E deck)
- Visual identity across all tracks in this run

### Inline Pre-Verification (Before Each Track)

**Before producing ANY track output, verify these 4 preconditions:**

| # | Check | How to Verify | Fail Action |
|---|-------|---------------|-------------|
| PV-1 | **Content package exists and has required layer** | Read `content_package.md`, confirm the layer this track needs is populated (Visual for image/deck, Data for data-report, Narrative for podcast/video). **Note:** Track J (interactive_report) can build qualitative dashboard from Core Layer alone — Data Layer optional. | Stop — fill the missing layer before building (except Track J qualitative mode) |
| PV-2 | **Direction is selected** | `direction_selected` variable or file exists from Direction Selection above | Stop — run Direction Selection first |
| PV-3 | **Output directory created** | `mkdir -p content/{name}/tracks/{track-dir}/` | Create it |
| PV-4 | **No stale output from prior run** | Check if track output already exists in target dir | Ask user: overwrite or skip? |

**Why this exists:** Phase 1-3 production runs revealed that skipping direction
selection or building against an empty Data Layer produces outputs that pass
individual RP checks but fail RP-X cross-format consistency. Pre-verification
catches these structural misses BEFORE wasting tokens on production.

---

### Track E: Deck (if "deck" in confirmed_tracks)

**Read the full track instructions:** `tracks/track-e-deck.md`

Produces leadership-ready PPTX with speaker notes and progressive reveal.
Combined model: PptxGenJS native elements + Playwright PNG for complex diagrams.
Visual QA subagent mandatory before delivery.

---

### Track F: PDF (if "one_pager" or "full_pdf" in confirmed_tracks)

**Read the full track instructions:** `tracks/track-f-pdf.md`

Two modes: one-pager (single A4, scannable) or full PDF (content-driven, no page limit).
HTML → Playwright PDF render with branded CSS from direction tokens.
Preview via s_pdf/scripts/convert_pdf_to_images.py.

---

### Track G: Data Report (if "data_report" in confirmed_tracks)

**Read the full track instructions:** `tracks/track-g-data-report.md`

openpyxl workbook with branded charts (via brand_chart.py), "so what" insight rows,
formulas over hardcoded values. Validated by s_xlsx/recalc.py.
Sheet count = data dimension count.

---

### Track H: Document (if "document" in confirmed_tracks)

**Read the full track instructions:** `tracks/track-h-document.md`

python-docx with branded styling, heading hierarchy, executive summary, TOC.
Content completeness: document length = what content needs. Appendix encouraged.
Tracked changes available via s_docx/scripts/document.py.

---

### Track I: AI Image (if "ai_image" in confirmed_tracks)

**Read the full track instructions:** `tracks/track-i-image.md`

Structured prompt generation for hero visuals. Tool-agnostic — detects DALL-E,
Stable Diffusion, or MCP image server at runtime. Falls back to prompt.json
export if no tool available. Used as supplier for other tracks (deck illustrations,
article headers, social thumbnails).

---

### Track J: Interactive Report (if "interactive_report" in confirmed_tracks)

**Read the full track instructions:** `tracks/track-j-interactive-report.md`

Single-file branded HTML with interactivity (tabs, expandable sections, traffic lights).
Built on s_html-artifact templates (base.css + report/scorecard/comparison). Direction
tokens override base palette. Three modes: Dashboard, Scorecard, Comparison.

---

### Track K: Podcast (if "podcast" in confirmed_tracks)

**Read the full track instructions:** `tracks/track-k-podcast.md`

Two-host dialogue script + optional MP3 audio via TTS. Detects edge-tts, OpenAI TTS,
or Amazon Polly at runtime. Falls back to script.json + transcript.md if no TTS.
Key principle: spoken ≠ written — short sentences, reactions, Host B challenges.

---

### Track L: HTML Deck (if "html_deck" in confirmed_tracks)

**Read the full track instructions:** `tracks/track-e2-html-deck.md`

Self-contained single-file HTML slide deck (fixed 1920×1080 stage, auto-scaled,
CSS-only animations) drawing from 34 bundled bold design systems. **Distinct from
Track E (PPTX):** Track L is a browser-viewable HTML deck, zero-network at render
(fonts bundled locally under `templates/html-deck/fonts/`). Ideal for web publishing
(swarm-content), live browser presenting, and PDF export via Playwright.

---

### Track A: Video (if "video" in production_tracks)

#### Step 5.1: Prerequisites Check

```bash
python "$SKILL_DIR/scripts/check_prereqs.py"
```

This verifies: Node.js, npm, ffmpeg, ffprobe, Python dependencies.

#### Step 5.2: Remotion Bootstrap (first run only)

```bash
STUDIO_DIR="$HOME/.swarm-ai/SwarmWS/Services/pollinate-studio"
if [ ! -f "$STUDIO_DIR/package.json" ]; then
  npx create-video@latest "$STUDIO_DIR" --template blank
  cd "$STUDIO_DIR" && npm install
  # Copy templates from skill
  cp -r "$SKILL_DIR/templates/remotion/"* "$STUDIO_DIR/src/remotion/"
fi
```

#### Step 5.3: Pronunciation Pre-Flight (zh-CN only)

Three-pass LLM analysis of podcast.txt:

**Pass 1: Polyphone scan** -- context-dependent disambiguation
- Example: 行 (hang=行业 vs xing=行动), 重 (zhong=重要 vs chong=重复)
- For each ambiguous character, determine correct pronunciation from context

**Pass 2: English term review** -- hyphenated names, initialisms
- Example: "GPT-SoVITS" -> split handling, "AIDLC" -> letter-by-letter
- Tag all English terms that TTS engines may mispronounce

**Pass 3: Brand names** -- words with expected Chinese pronunciation
- Example: "Qwen" -> "qian wen", "Doubao" -> "dou bao"
- Cross-reference with brand/identity.yaml voice section

**Output:** `content/{name}/tracks/video/phonemes.json`

**Phoneme priority:** inline `word[pinyin]` > project phonemes.json > global phonemes.json

#### Step 5.4: TTS Audio Generation

```bash
python "$SKILL_DIR/scripts/generate_tts.py" \
  --input "content/{name}/tracks/video/podcast.txt" \
  --output-dir "content/{name}/tracks/video/" \
  --backend polly \
  [--resume]
```

**Backend resolution:** CLI `--backend` > env `TTS_BACKEND` > `user_prefs.json` > `"edge"` (default, free)

**Outputs:**
- `podcast_audio.wav` -- full narration audio
- `podcast_audio.srt` -- subtitle file
- `timing.json` -- per-section timestamps for Remotion sync

**CRITICAL: Save timing.json BEFORE audio concatenation.** If concatenation
fails or is interrupted, timing data is lost and must be regenerated from
scratch. The script handles this, but verify the file exists after TTS completes.

#### Step 5.5: Thumbnail Generation

Always generate via Remotion still render:
```bash
cd "$STUDIO_DIR"
npx remotion still src/remotion/index.ts Thumbnail16x9 \
  "content/{name}/tracks/video/thumbnail_16x9.png" \
  --public-dir "content/{name}/tracks/video/" \
  --props '{"title": "{Title}", "theme": "{theme}"}'
npx remotion still src/remotion/index.ts Thumbnail4x3 \
  "content/{name}/tracks/video/thumbnail_4x3.png" \
  --public-dir "content/{name}/tracks/video/" \
  --props '{"title": "{Title}", "theme": "{theme}"}'
```

For 小红书, also generate 3:4:
```bash
npx remotion still src/remotion/index.ts Thumbnail3x4 \
  "content/{name}/tracks/video/thumbnail_3x4.png" \
  --public-dir "content/{name}/tracks/video/" \
  --props '{"title": "{Title}", "theme": "{theme}"}'
```

#### Step 5.6: Remotion Composition

1. Copy component library to studio (if absent or updated):
   ```bash
   cp -r "$SKILL_DIR/templates/remotion/components/"* "$STUDIO_DIR/src/remotion/components/"
   ```
2. Create per-video composition: `src/remotion/{PascalCaseName}Video.tsx`
   - **NEVER overwrite Video.tsx template** -- create a new file per video
   - Register the new composition in Root.tsx
3. Apply visual preferences from `brand/identity.yaml` + `user_prefs.json`
4. Drive all animations from `timing.json` via `useTiming` hook
5. 4K output: design at 1080p, wrap in `<Scale4K>` component (`scale(2)` to 3840x2160)
6. Subtitles + ChapterProgressBar render OUTSIDE `<Scale4K>` wrapper

#### Step 5.7: Studio Preview (MANDATORY GATE)

```bash
cd "$STUDIO_DIR"
pkill -f "remotion studio" 2>/dev/null || true
npx remotion studio src/remotion/index.ts --public-dir "content/{name}/tracks/video/"
```

**THIS GATE CANNOT BE SKIPPED. NO AUTOMATION BYPASSES IT.**

- Agent reviews each section for timing sync, visual quality, subtitle placement
- User reviews in Remotion Studio (opens browser at localhost:3000)
- Iterate on any issues found
- Pipeline BLOCKS here until user explicitly says one of:
  - "render 4K"
  - "render final"
  - "looks good, proceed"
  - "approved"

**If user has not approved, DO NOT advance to REVIEW. Period.**

### Decisions

| Decision | Classification | Default |
|----------|---------------|---------|
| TTS backend selection | Mechanical | user_prefs > env > "edge" |
| Speech rate | Taste | "+0%" (from identity.yaml) |
| Color theme | Taste | From domain_themes in identity.yaml |
| Component animation style | Taste | Default animations per component |
| Subtitle font/size | Mechanical | From user_prefs.global.subtitle |

### Verification Gate

Before advancing to REVIEW, ALL must be true:
- [ ] `podcast_audio.wav` exists and is non-empty
- [ ] `timing.json` exists and has entries for ALL `[SECTION:xxx]` markers
- [ ] `podcast_audio.srt` exists and has > 0 subtitle entries
- [ ] All thumbnail files exist: `thumbnail_16x9.png`, `thumbnail_4x3.png` (and `thumbnail_3x4.png` if 小红书 targeted)
- [ ] Per-video `.tsx` composition file exists in `src/remotion/`
- [ ] Composition is registered in `Root.tsx`
- [ ] `phonemes.json` exists (zh-CN) or pronunciation pre-flight was N/A (en-US)
- [ ] User has explicitly approved the Studio preview ("render 4K" or equivalent)
- [ ] Timing sync verified: each section start/end in timing.json matches audio sections

### Anti-Rationalization

| Shortcut | Required Response |
|----------|-------------------|
| "Studio preview looks fine from the terminal output" | Terminal output is not visual review. Open Studio, check every section. |
| "User hasn't responded, assume approval" | Approval must be EXPLICIT. Wait or checkpoint. |
| "Polyphone check is overkill for a short script" | Short scripts have higher per-word impact. One mispronounced term ruins credibility. |
| "Skip thumbnails, we can add them later" | Thumbnails drive click-through rate. Generate all sizes now. |
| "TTS audio sounds fine, skip timing verification" | Timing drift compounds. Verify timing.json matches every section marker. |
| "Remotion template works fine, no need for per-video composition" | Templates are shared. Per-video compositions allow content-specific layout. Always create one. |

### Max Retries

3. After exhaustion -> checkpoint.

### Output Files

- `content/{name}/tracks/video/podcast_audio.wav` -- full narration audio
- `content/{name}/tracks/video/podcast_audio.srt` -- subtitle file
- `content/{name}/tracks/video/timing.json` -- per-section timestamps
- `content/{name}/tracks/video/phonemes.json` -- pronunciation corrections (zh-CN)
- `content/{name}/tracks/video/thumbnail_16x9.png` -- 1920x1080 playback thumbnail
- `content/{name}/tracks/video/thumbnail_4x3.png` -- 1200x900 recommendation feed
- `content/{name}/tracks/video/thumbnail_3x4.png` -- 1080x1440 小红書 feed (if applicable)
- `$STUDIO_DIR/src/remotion/{Name}Video.tsx` -- per-video Remotion composition

---

### Track B: Poster (if "poster" in production_tracks)

#### Step B.1: Load Design System

```
Read brand/poster_design_system.md
```

This is MANDATORY before writing any poster HTML. Do not skip. The design system
defines spacing tokens, typography scale, alignment rules, and anti-patterns.

#### Step B.2: Select Directions (ALWAYS 2 variants)

**MANDATORY: Every poster run produces exactly 2 direction variants.**

Select 2 best-fit directions from the content:
1. Read each direction's `content_triggers` in `brand/directions/d{N}-{name}.yaml`
2. Score content against triggers (strong=3, moderate=2, weak=1)
3. Top 2 scores → selected directions
4. If user specified a direction → that's #1, auto-select #2 based on content contrast

**Direction selection decision: Mechanical** (score-based from triggers).

#### Step B.3: Author HTML (per direction)

For EACH selected direction, create `content/{name}/tracks/poster/{topic}-{direction}.html`:

- Load direction's `css_snippet` into `:root {}` block
- Add `<!-- Direction: D{N} {name} -->` as first HTML comment
- ALL colors via `var(--token)` — **ZERO hardcoded hex in body**
- ALL text-align: center (no exceptions, no mixed alignment)
- Section gaps ≤ 72px (no explicit dividers between sections)
- Typography: headline 52px / body 22px / eyebrow 12px
- Card variety: NEVER same style consecutively (per direction's `card_styles`)
- Chinese body line-height: 2.0 minimum
- Max text width: 700px
- Include Default Branding (see `poster_design_system.md`):
  - Footer section with 🐝 + SwarmAI + tagline + QR + github link
  - Watermark: `🐝 Made with SwarmAI Pollinate` at bottom-right

**QR code:** Use pre-rendered asset from `brand/assets/logo/`:
- Light bg directions (D2/D3/D5): `qr-github-dark-on-light.png`
- Dark bg directions (D1/D4): `qr-github-light-on-dark.png`

#### Step B.4: Render to PNG (per direction)

```bash
python3 -c "
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={'width': 1080, 'height': 800})
    page.goto('file://{absolute_path_to_html}')
    page.wait_for_timeout(800)
    page.screenshot(path='{output_png}', type='png', full_page=True)
    browser.close()
"
```

#### Step B.5: 8-Layer Quality Convergence Loop (BLOCKING)

> "Content as Black Box" — same principle as Pipeline's 6-Layer Push-Ready Gate.
> Output must pass ALL 8 layers simultaneously before reaching the user.
> A poster passing 7/8 is NOT publish-ready.

**For EACH rendered variant, run all 8 layers:**

| Layer | Gate | What to Verify | RP-P | Auto-fixable? |
|-------|------|---------------|------|---------------|
| L1 | Direction Declared | HTML has `<!-- Direction: D{N} -->` comment | RP-P1 | ✅ Inject comment |
| L2 | Token Purity | Zero hardcoded hex values in `<style>` body (outside `:root`) | RP-P2 | ✅ Replace with var() |
| L3 | Spacing Compliance | All section gaps ≤ 72px when rendered | RP-P3 | ⚠️ Reduce padding |
| L4 | Alignment Unity | ALL text elements use text-align: center | RP-P4 | ✅ Force center |
| L5 | Anti-Slop Clean | Zero violations against Visual + Structural Ban Lists | RP-P5 | ⚠️ Regenerate section |
| L6 | Platform Fit | Output width = 1080px, file < 2MB | RP-P6 | ✅ Re-render viewport |
| L7 | Brand Present (MANDATORY) | Watermark `🐝 Made with SwarmAI Pollinate` + QR code linking to `github.com/xg-gh-25/SwarmAI` + GitHub URL text — ALL must exist in DOM. **No exceptions.** | RP-P7 | ✅ Append template |
| L8 | 2-Variant Output | ≥ 2 direction PNGs rendered | — | ✅ Render second |

**Convergence Loop:**

```
LOOP (max 3 iterations):
  1. Run all 8 layers against rendered PNG + source HTML
  2. IF all 8 PASS → exit loop, proceed to Content Principles Check
  3. IF failures exist:
     a. Auto-fixable (L1,L2,L4,L6,L7,L8): fix HTML in-place
     b. Semi-fixable (L3,L5): adjust CSS values
     c. Re-render via Playwright
     d. Re-verify ALL 8 layers (fix may introduce new failures)
  4. Increment iteration counter

EXIT CONDITIONS:
  - All 8 pass → PUBLISH-READY (proceed)
  - 3 iterations without convergence → show best version + flag remaining issues

CRITICAL: The loop runs BEFORE the user sees anything.
The user receives only publish-ready output.
```

**Automated enforcement script (run BEFORE manual checks):**

```bash
python "$SKILL_DIR/scripts/convergence_gate.py" "content/{name}/tracks/poster/{topic}-{direction}.html" --json
```

Returns JSON: `{"valid": true/false, "checks_passed": N, "checks_total": 8, "errors": [...], "layers": {...}}`
Exit 0 = all 8 pass. Exit 1 = failures listed. Run this FIRST — it catches mechanical
violations instantly. Only proceed to manual verification for issues the script cannot
detect (visual aesthetics, content quality).

**Verification methods (manual fallback for when script is unavailable):**

L1: `grep "<!-- Direction:" {html_file}`
L2: After `</style>`, scan body for raw hex: `#[0-9a-fA-F]{3,8}` → 0 matches (exclude img src paths)
L3: Check section padding values in CSS (all ≤ 72px for between-section gaps)
L4: **TWO checks required:**
    (a) All section/container CSS classes (.s, .hero, .card, section) MUST have explicit `text-align:center` in their class definition — never rely on inheritance or browser default (which is left)
    (b) `grep "text-align" {html}` → only "center" and "right" (watermark only). Any "left" or "justify" → FAIL
    WHY BOTH: A class without text-align declaration inherits browser default (left). Grepping only for declared values misses this. The fix is to require explicit declaration on every container.
L5: Scan against `poster_design_system.md` ban lists (32 visual + 13 structural)
L6: Rendered PNG width = 1080px AND file size < 2MB
L7: `grep "Made with SwarmAI Pollinate" {html}` + `grep "qr-github" {html}`
L8: Count `*-d*.png` files in output directory ≥ 2

#### Step B.5b: Adversarial Brand Review (BLOCKING — sub-agent)

> Same pattern as Pipeline's adversarial specialist dispatch (deliver.md).
> Fresh-context sub-agent catches what self-review structurally cannot:
> brand drift, unconscious style mixing, spacing assumptions from builder bias.

**After the 8-Layer Gate passes (all 8 green), spawn an adversarial sub-agent:**

Use the Agent tool with a **fresh context** (the sub-agent has NOT seen the
BUILD process — it reviews cold, like an external brand auditor).

**Sub-agent prompt template:**

```
You are a brand consistency reviewer for SwarmAI Pollinate.
Review this poster HTML against the brand system and quality patterns.
Be specific: element/line, what's wrong, how to fix.

## Brand System (source of truth)
<paste contents of brand/poster_design_system.md>

## Content Principles
<paste contents of brand/content_principles.md>

## Quality Patterns (RP-P1~P7)
<paste REVIEW_PATTERNS.md poster section>

## Poster HTML to Review
<paste the rendered HTML source for EACH variant>

## Output
For each finding, output:
- Severity: HIGH (publish-blocking) / MED (should fix) / LOW (polish)
- Element: CSS selector or text content that violates
- Rule: which RP-P# or brand rule is violated
- Fix: specific CSS/HTML change to resolve

If no findings: output `BRAND CLEAN — no violations detected`.

Focus on:
1. Direction mixing (elements from one direction bleeding into another)
2. Spacing/alignment drift (gaps > 72px, non-centered text)
3. Color token violations (hardcoded values that passed L2 somehow)
4. Content principle violations (P1-P8 in copy text)
5. Typography scale violations (font sizes outside design system range)
6. Visual ban list items that may have been missed by mechanical check
```

**Sub-agent configuration:**
- Use default model (opus for strongest visual reasoning)
- Do NOT run in background — must complete before DELIVER
- If sub-agent returns findings:
  - HIGH severity → fix in HTML, re-render, re-verify 8-Layer Gate
  - MED severity → fix if convergence iterations remain, else note in REPORT
  - LOW severity → note only
- If sub-agent returns `BRAND CLEAN` → proceed to Content Principles Check

**Why this exists:** The builder (you) wrote the HTML and can't see your own
assumptions. You might use the correct token variable but the visual result
clashes. You might declare Direction D4 but unconsciously use D5's spacing
rhythm. The 8-Layer Gate catches MECHANICAL violations (hex values, missing
elements). The sub-agent catches AESTHETIC violations (does this LOOK like the
declared direction? does the spacing FEEL consistent?).

**Record results in convergence-log.txt:**
```
Adversarial Brand Review:
  Spawned: yes
  Findings: N (H:X M:Y L:Z)
  Fixed: N | Noted: N
  Status: CLEAN / FIXED / ESCALATED
```

---

#### Step B.6: Content Principles Check (external content only)

Read `brand/content_principles.md` and run anti-pattern scan on poster text:
- No LOC/commit/天数 as value (P1)
- No first-person hero framing (P2) — **MECHANICAL GATE: run `scripts/p2_scan.py`**
- Thesis-driven, not feature-driven (P3)
- Effects over mechanisms (P4)
- English only where stronger than Chinese (P5)
- Each piece standalone (P6)
- No internal术语 in body text (P7)
- Positioning hierarchy respected (P8)

**P2 Hero Framing Gate (BLOCKING — L2 mechanical enforcement):**
```bash
# Scan poster HTML for hero framing (script auto-strips HTML tags/style/script)
python3 scripts/p2_scan.py {html_file}
```
Exit 0 = clean. Exit 1 = FAIL — fix offending text before proceeding.
Targets: "我造了/我做了/我们是最.../我们的X远超" hero claims.
Does NOT flag: section headers ("## 我们的设计哲学"), technical discussion, thesis statements.

**Legacy term blocklist (mechanical check):**
```
BLOCKED = ["Your AI Team, 24/7", "AI 实践者，不是布道者"]
```
Any match → FAIL → fix before proceeding.

#### Poster Decisions

| Decision | Classification | Default |
|----------|---------------|---------|
| Direction selection | Mechanical | Score-based from content_triggers |
| Card layout choice | Taste | From direction's card_styles |
| Visual element style | Taste | From direction's visual_elements |
| QR code variant | Mechanical | Light/dark based on direction bg |
| Color token usage | Mechanical | Must be var(--token), zero hardcoded |

#### Poster Output Files (per run)

- `content/{name}/tracks/poster/{topic}-d{N}-{name}.html` -- source (2 files)
- `content/{name}/tracks/poster/{topic}-d{N}-{name}.png` -- rendered (2 files)
- `content/{name}/tracks/poster/convergence-log.txt` -- gate results per iteration

#### Step B.7: Delegation Fidelity Check (Reskin/Restyle Operations)

**BLOCKING — applies whenever a poster is reskinned from one direction to another.**

When restyling existing content (e.g., "reskin D2 → D5"), the output MUST preserve
≥90% of the source content's structural sections. Content truncation during style
changes is the #1 delegation failure mode (C024, 2026-05-16: all 4 posters were
truncated by 40-60% during D5 reskin).

**Verification procedure (after reskin):**
```bash
# Count sections in source HTML (grep -E for alternation on macOS BSD grep)
SOURCE_SECTIONS=$(grep -Ec '<section|<div class="s|<div class="card' {source_html})
# Count sections in output HTML
OUTPUT_SECTIONS=$(grep -Ec '<section|<div class="s|<div class="card' {output_html})
# Calculate ratio
RATIO=$(python3 -c "print(f'{$OUTPUT_SECTIONS / max($SOURCE_SECTIONS, 1) * 100:.0f}%')")
echo "Delegation fidelity: $OUTPUT_SECTIONS / $SOURCE_SECTIONS sections = $RATIO"
```

**Gate:**
- Ratio ≥ 90% → PASS (minor structural simplification is acceptable)
- Ratio < 90% → **FAIL** — content was truncated. Regenerate from source, preserving
  ALL section content. Only change: CSS tokens, colors, typography.
- Ratio > 110% → WARN — content was added (may be intentional for enrichment)

**When this fires:**
- "Reskin to D5 style" / "switch direction" / "restyle"
- Any operation that takes existing HTML as input and produces restyled output
- Sub-agent delegation for reskin tasks

**Why this exists:** Agent delegation (sub-agent or same-agent multi-pass) systematically
truncates content when given a style-change task. The agent optimizes for visual
coherence in the new style and unconsciously drops sections that feel "redundant"
in the new layout. This gate makes truncation mechanically detectable.

### Track C: Narrative / Article (if "narrative" in production_tracks)

#### Step C.1: Content Generation

Write the article/narrative in markdown format. Follow content_package.md
structure and content_principles.md for tone.

#### Step C.2: L9 GEO Signal Stack (ADVISORY — articles/narratives only)

> GEO (Generative Engine Optimization) ensures content is citable by AI engines
> (ChatGPT, Perplexity, Claude, Gemini). Source: Princeton KDD 2024 + CMU AutoGEO.

**After writing narrative content, run the GEO scorer:**

```bash
python "$SKILL_DIR/scripts/geo_score.py" "content/{name}/tracks/narrative/{file}.md" --json
```

**Output:** JSON with `total_score` (0-100), per-pillar breakdown, warnings.
**Gate:** Score ≥ 60 = PASS. Score < 60 = WARN (advisory, not blocking for now).

**The 4 pillars (weighted):**

| Pillar | Weight | Key Checks |
|--------|--------|------------|
| Evidence Density | 35% | ≥5 numbers with units, ≥2 named experts, ≥1 cite/500w |
| Structure & Position | 25% | Core message in first 150 words (PAWC), TL;DR section, headings |
| Authority Signals | 25% | Author byline, date, methodology section, limitations |
| AI Crawlability | 15% | Clean markdown, semantic headings, no placeholders |

**PAWC Rule (Position-Adjusted Word Count):**
Sentence #1 in AI answers is worth ~5x sentence #20 (exponential decay).
**Put the conclusion + one statistic in the first 150 words. Always.**

**If score < 60 (advisory improvements):**
- Add specific numbers with units (not "many" or "significant")
- Add named expert quotes (not "experts say")
- Move core finding to first paragraph
- Add TL;DR section for articles > 500 words
- Remove generic openers ("In today's rapidly evolving...")

**Anti-patterns (auto-detected, surfaced as warnings):**
- Generic AI openers ("In today's rapidly evolving landscape...")
- Unsupported superlatives ("revolutionary", "game-changing") without evidence
- Zero named entities (content too generic to be cited)
- Keyword density > 1% (actively harmful per Princeton data)

#### Narrative Output Files

- `content/{name}/tracks/narrative/{topic}.md` — the article
- `content/{name}/tracks/narrative/geo_score.json` — GEO scoring result (saved automatically)

---

## Stage 6: REVIEW -- Quality Audit

### Procedure

1. **Load REVIEW_PATTERNS.md** and run ALL 12 RP-V patterns. For each pattern,
   write explicit pass/fail with evidence:

   | # | Pattern | What to Verify |
   |---|---------|----------------|
   | RP-V1 | **Audio-video sync** | timing.json: each section start/end within +/-0.5s of audio |
   | RP-V2 | **Subtitle safe zone** | No visual content in bottom 100px (reserved for subtitles) |
   | RP-V3 | **Information density** | Each screen shows <= 3 key points simultaneously |
   | RP-V4 | **Subtitle accuracy** | SRT text vs podcast.txt: diff <= 2% (character-level) |
   | RP-V5 | **Thumbnail specs** | 16:9 AND 4:3 files exist, correct dimensions. 3:4 for 小红书 |
   | RP-V6 | **Polyphone coverage** | All domain-specific terms in phonemes.json (zh-CN only) |
   | RP-V7 | **Resolution & codec** | ffprobe: 3840x2160 (or 2160x3840), H.264, >= 8Mbps video, AAC >= 192kbps |
   | RP-V8 | **Duration target** | B站: 3-12min, shorts: 30-120s per section |
   | RP-V9 | **Brand consistency** | Swarm color palette (identity.yaml), font family, intro/outro present |
   | RP-V10 | **Component variety** | No same component type in consecutive sections |
   | RP-V11 | **Text readability** | All text >= 24px, hero >= 84px, section title >= 72px |
   | RP-V12 | **Content width** | >= 85% of screen width utilized |

1b. **If format includes poster**, additionally run poster patterns (RP-P):

   | # | Pattern | What to Verify |
   |---|---------|----------------|
   | RP-P1 | **Alignment consistency** | All sections use same alignment system (center). No mixed left/center. |
   | RP-P2 | **Spacing rhythm** | All vertical spacing is a multiple of base unit (24px). No arbitrary values. |
   | RP-P3 | **Text max-width** | Body text never exceeds 700px. Headline never exceeds 800px. |
   | RP-P4 | **Brand colors** | All colors match `brand/identity.yaml` or `poster_design_system.md` tokens exactly. |
   | RP-P5 | **Content principles** | Anti-pattern checklist from `brand/content_principles.md` passes (P1-P8). |
   | RP-P6 | **Platform compliance** | PNG < 2MB, width = 1080px, no text < 20px (unreadable on phone). |
   | RP-P7 | **Legacy term blocklist** | ZERO matches against: "Your AI Team, 24/7", "AI 实践者，不是布道者". |

2. **Output format** -- write result for EVERY pattern:
   ```
   RP-V1:  PASS  All 6 sections within +/-0.3s
   RP-V2:  PASS  Bottom 100px clear
   RP-V3:  WARN  Section 3 has 4 points -- consider splitting
   RP-V4:  PASS  SRT diff 0.8%
   RP-V5:  PASS  16:9 (1920x1080), 4:3 (1200x900), 3:4 (1080x1440)
   RP-V6:  PASS  12 terms in phonemes.json
   RP-V7:  PASS  3840x2160, H.264, 16.2Mbps, AAC 192kbps
   RP-V8:  PASS  6:42 (within 3-12min)
   RP-V9:  PASS  Swarm Orange #FF6B35, PingFang SC, outro present
   RP-V10: PASS  FlowChart -> QuoteBlock -> Timeline -> CodeBlock -> StatCounter
   RP-V11: PASS  Min text 32px, hero 96px
   RP-V12: PASS  Content width 88%
   ```

3. **For each FAIL result:**
   - Identify the exact issue
   - Fix it immediately (adjust composition, re-generate asset, trim script)
   - Re-verify after fix
   - Log the fix

4. **For each WARN result:**
   - Assess severity
   - Fix if clearly wrong; log as taste decision if borderline
   - Document reasoning

5. **Audience Simulation (spawn subagent — advisory, single-pass)**

   After technical QA passes, test whether the content captures attention — not just whether it's correct.

   **When to run:** Always, unless `strategy.json` has `"internal": true` or `channel_matrix` is empty.

   **Persona source:** Construct from `research.md` Target Audience Profile + the platform being reviewed from `channel_matrix`. Run ONCE per primary platform (not per variant).

   **Mechanism:** Use the Agent tool. Do NOT run in background. Send ONLY the rendered asset (PNG for posters, caption text for social, narrative text for articles — never send source HTML/code). Record results in `content/{name}/review_results.md` under "Audience Simulation" heading.

   **Prompt:**
   ```
   You are [PERSONA from research.md — e.g., "a tech lead scrolling LinkedIn at 8am"].

   Based on the visual hierarchy and information density, assess whether the
   primary message is extractable in under 3 seconds of viewing.

   Report:
   1. Would this stop your scroll? Why or why not?
   2. What's the ONE takeaway from the visual/text hierarchy?
   3. Does anything feel generic, AI-generated, or "seen this before"?
   4. What would make you share this with a colleague?

   Be brutally honest. "It's fine" is not useful feedback.
   ```

   **How to use results (ADVISORY ONLY — do NOT rework based on this alone):**
   - All results logged as **taste decisions** for the Delivery Gate
   - If #1 AND #3 both flag negative → escalate as HIGH taste decision (user decides at gate)
   - Otherwise → log observation, proceed to Verification Gate

   **Run exactly ONCE.** Do not re-simulate after any fixes. This is a signal, not a loop.

### Decisions

| Decision | Classification | Default |
|----------|---------------|---------|
| Fix FAIL findings | Mechanical | Must fix all |
| Fix WARN findings | Taste | Fix unless borderline |
| "Information density is 4 points but they're related" | Taste | Split if possible |
| "Brand color is #FF6C36, close to #FF6B35" | Mechanical | Must match exactly |
| Act on Audience Simulation feedback | Taste | Surface at Delivery Gate; fix only if #1 AND #3 both negative |
| "Audience is too niche for simulation" | Mechanical | If channel_matrix has platforms → run it |

### Verification Gate

Before advancing to TEST, ALL must be true:
- [ ] All applicable RP results shown (RP-V for video, RP-P for poster — every pattern has a result)
- [ ] Zero FAIL results remain unfixed
- [ ] All WARN results have documented reasoning (fix or accepted with justification)
- [ ] Brand colors match identity.yaml exactly (not "close enough")
- [ ] (Video) Subtitle safe zone verified (no visual content in bottom 100px)
- [ ] (Video) Audio-video sync verified per section
- [ ] (Poster) Alignment is consistent — no mixed left/center
- [ ] (Poster) Legacy term blocklist passes (zero matches)
- [ ] (Poster) Content principles anti-pattern checklist passes
- [ ] Audience Simulation results logged in review_results.md (or skipped: `"internal": true` / empty channel_matrix)

### RP-X: Cross-Format Consistency (Multi-Track Runs Only)

**When to run:** Only if `confirmed_tracks` has 2+ entries in discovery.json.
**When to skip:** Single-track runs (no cross-format to check).

Run the cross-format consistency checker:

```bash
python "$SKILL_DIR/scripts/cross_format_check.py" "content/{name}/" --json
```

This verifies 5 RP-X patterns:

| # | Pattern | What it catches |
|---|---------|-----------------|
| RP-X1 | **Brand token consistency** | Different direction YAML applied to different tracks |
| RP-X2 | **Message alignment** | Thesis drift — one track strayed from core message |
| RP-X3 | **Data integrity** | Same metric shows different values across formats |
| RP-X4 | **Naming conventions** | Spaces in filenames, inconsistent casing |
| RP-X5 | **Visual coherence** | Completely different color palettes across visual formats |

**Result handling:**
- FAIL → fix immediately (same treatment as RP-V/RP-P FAIL)
- WARN → log as taste decision, surface at Delivery Gate
- SKIP → expected for non-applicable checks (e.g., no HTML outputs)
- PASS → no action needed

### Anti-Rationalization

| Shortcut | Required Response |
|----------|-------------------|
| "Script is short, skip polyphone check" | Short scripts have higher per-word impact. Check every term. |
| "This is internal, skip Audience Simulation" | If channel_matrix has platforms, it's external. Run simulation. |
| "Audience is too niche for simulation to work" | Niche audiences are EASIER to simulate precisely. Run it. |
| "Brand colors are close enough" | Brand consistency is binary. Match identity.yaml hex values or fix. |
| "Duration is 12:30, close enough to 12min" | 12:00 is the max. Trim the script. |
| "It looked fine in Studio, skip review" | Studio preview is not quality audit. Check every RP pattern. |
| "Only targeting B站, skip other platform specs" | Generate metadata for all platforms. Distribution is free. |
| "Thumbnails can wait" | Thumbnails drive click-through rate. Verify all sizes exist now. |

### Max Retries

2. After exhaustion -> checkpoint.

### Output Files

- Review results appended to `content/{name}/review_results.md`
- Any fixed assets (updated composition, re-rendered thumbnails, trimmed script)

---

## Stage 7: TEST -- Render + Platform Validation

### Procedure

#### Step 7.1: 4K Render

```bash
cd "$STUDIO_DIR"
npx remotion render src/remotion/index.ts {CompositionId} \
  "content/{name}/tracks/video/output.mp4" \
  --video-bitrate 16M \
  --public-dir "content/{name}/tracks/video/"
```

#### Step 7.2: Verify Render Output

```bash
ffprobe -v quiet -show_entries stream=width,height,codec_name,bit_rate -of json \
  "content/{name}/tracks/video/output.mp4"
```

**Required results:**
- Width: 3840, Height: 2160 (horizontal) or 2160x3840 (vertical)
- Codec: h264
- Video bitrate: >= 8Mbps
- Audio codec: aac

If ffprobe fails any check -> fix and re-render.

#### Step 7.3: BGM Mix

```bash
BGM_VOL=$(python "$SKILL_DIR/scripts/get_pref.py" global.bgm.volume 2>/dev/null || echo "0.05")
ffmpeg -y \
  -i "content/{name}/tracks/video/output.mp4" \
  -i "$SKILL_DIR/brand/assets/bgm/calm-piano.mp3" \
  -filter_complex "[1:a]volume=${BGM_VOL}[bgm];[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=2[a]" \
  -map 0:v -map "[a]" \
  -c:v copy -c:a aac -b:a 192k \
  "content/{name}/tracks/video/video_with_bgm.mp4"
```

#### Step 7.4: Subtitles (optional)

Prefer Remotion-native `<Subtitles>` component (no re-encode needed).
Fallback if not using Remotion subtitles:
```bash
ffmpeg -y \
  -i "content/{name}/tracks/video/video_with_bgm.mp4" \
  -vf "subtitles=content/{name}/tracks/video/podcast_audio.srt:force_style='FontName=PingFang SC,FontSize=24,PrimaryColour=&HFFFFFF&,OutlineColour=&H000000&,Outline=2,Bold=1,Alignment=2,MarginV=30'" \
  -c:v libx264 -crf 18 -c:a copy \
  "content/{name}/tracks/video/video_with_subs.mp4"
```

#### Step 7.5: Final Assembly

```bash
cp "content/{name}/tracks/video/video_with_bgm.mp4" "content/{name}/tracks/video/final_video.mp4"
```

(If subtitle burn-in was used, copy `video_with_subs.mp4` instead.)

#### Step 7.6: Platform Spec Validation

```bash
python "$SKILL_DIR/scripts/check_specs.py" \
  "content/{name}/tracks/video/final_video.mp4" \
  --platforms bilibili,youtube,xiaohongshu,douyin,weixin_video
```

**Platform spec requirements:**

| Check | B站 | YouTube | 小红書 | 抖音 | 视频号 |
|-------|-----|---------|--------|------|--------|
| Resolution | 3840x2160 | 3840x2160 | 2160x3840 | 2160x3840 | 2160x3840 |
| Codec | H.264 | H.264 | H.264 | H.264 | H.264 |
| Bitrate | >= 8Mbps | >= 8Mbps | >= 6Mbps | >= 6Mbps | >= 6Mbps |
| Duration | 3-12min | 3-12min | 30-120s | 30-120s | 30-120s |
| Audio | AAC 192k | AAC 192k | AAC 192k | AAC 192k | AAC 192k |

Must pass ALL checks for ALL target platforms.

### Decisions

| Decision | Classification | Default |
|----------|---------------|---------|
| All spec validation | Mechanical | Binary pass/fail |
| BGM volume level | Taste | 0.05 from identity.yaml |
| Subtitle burn-in vs Remotion-native | Mechanical | Remotion-native preferred |

### Verification Gate

Before advancing to DELIVER, ALL must be true:
- [ ] ffprobe confirms 3840x2160 (or 2160x3840), H.264, >= 8Mbps, AAC >= 192kbps
- [ ] `check_specs.py` passes for ALL target platforms
- [ ] `final_video.mp4` exists and is playable
- [ ] BGM is mixed (voice audible, BGM subtle)
- [ ] Video duration matches dry-run estimate (+/- 10%)
- [ ] No rendering artifacts (black frames, frozen sections, audio gaps)

### Anti-Rationalization

| Shortcut | Required Response |
|----------|-------------------|
| "ffprobe shows 1920x1080, that's HD enough" | 4K is the spec. Re-render at 3840x2160. |
| "Platform specs mostly pass" | ALL must pass. Fix every failure. |
| "BGM sounds fine at default volume" | Verify voice is clearly audible. If technical content, consider lower BGM. |
| "Skip subtitle verification, we used Remotion-native" | Remotion subtitles still need accuracy check. Compare SRT against script. |
| "Video plays in my player, skip ffprobe" | Your player is tolerant. Platforms are not. ffprobe is the authority. |

### Max Retries

3. After exhaustion -> checkpoint.

### Output Files

- `content/{name}/tracks/video/output.mp4` -- raw 4K render
- `content/{name}/tracks/video/video_with_bgm.mp4` -- with background music
- `content/{name}/tracks/video/final_video.mp4` -- final deliverable
- Platform spec validation results (printed to console)

---

## Stage 8: DELIVER -- Publish Package + Report

### Procedure

#### Step 8.1: Run the Delivery Gate FIRST

Collect ALL taste decisions from ALL prior stages and present as a batch:

```
DELIVERY GATE -- N taste decisions for review:

  1. [THINK]   Differentiation angle: "两大框架创始人同时验证"
  2. [PLAN]    Used dark theme for AI architecture topic
  3. [PLAN]    5 sections instead of 7 (tighter narrative)
  4. [BUILD]   Speech rate +10% for technical content
  5. [BUILD]   FlowChart over DiagramReveal (simpler animation)

  [Approve All]  [Override #1]  [Override #2]  ...  [Discuss]
```

**If no taste decisions accumulated:** skip the gate, proceed.

**If user approves all:** proceed.

**If user overrides any:** re-run the affected stage with the override as a
constraint. This may cascade (overriding a PLAN decision re-runs PLAN, which
may change BUILD). Re-run the minimum set of affected downstream stages.

**If user wants to discuss:** enter conversational mode. Once resolved, resume.

#### Step 8.2: Generate Platform Metadata

```bash
python "$SKILL_DIR/scripts/publish_meta.py" \
  "content/{name}/" \
  --platforms bilibili,youtube,xiaohongshu,douyin,weixin_video
```

Output: `content/{name}/deliver/publish_info.md` with per-platform:

| Platform | Title Rules | Description | Tags | CTA |
|----------|-------------|-------------|------|-----|
| B站 | Number + topic + hook (max 80 chars) | 100-200 chars, knowledge style | 10 tags | 一键三连 |
| YouTube | SEO < 70 chars | Keyword-rich + chapters from 0:00 | Tags + hashtags | Subscribe |
| 小红書 | <= 20 chars, emoji-friendly | 200-500 chars, 种草 style | 5-10 `#tag#` | 点赞收藏加关注 |
| 抖音 | Short, punchy | 100-200 chars, casual + emoji | 3-8 `#tag` | 点赞关注 |
| 视频号 | Knowledge-sharing | 100-300 chars, forwarding-friendly | 3-8 `#tag` | 点赞关注转发 |

#### Step 8.2.1: 小红书 Multi-Image Publish Kit

小红书 content is image-first. Every 小红书 delivery produces a **3-part publish kit**:

**Part 1 — Post Text** (copy-paste ready):
- **Title:** <= 20 chars, punchy, emoji optional
- **Briefing Summary:** 200-500 chars, conversational 种草 style, use 👉 for key points, end with CTA
- **Tags:** 5-10 `#tag#` format, mix broad + specific

**Part 2 — Posters** (N standalone images based on complexity):
Posters are single-screen information-dense images that can stand alone in feeds.

| Complexity | Poster Count | Content |
|------------|-------------|---------|
| Simple (1 product, 1 angle) | 1 | Cover: title + core visual + CTA |
| Medium (comparison, 2-3 points) | 2 | Cover + data/comparison chart |
| Complex (multi-product, multi-dimension) | 3 | Cover + detailed matrix + core insight highlight |

Each poster: 1080×1440 (3:4), dark theme, `@2x` retina rendering via Chrome headless.

**Part 3 — Deep Article Cards** (N images based on content length):
Cards form a swipeable long-read experience. Each card = one logical section.

| Content Length | Card Count | Typical Breakdown |
|---------------|-----------|-------------------|
| Short (< 500 words) | 4-5 | Cover + 2-3 body + CTA |
| Medium (500-1500 words) | 6-8 | Cover + sections + data + quotes + CTA |
| Long (> 1500 words) | 9-12 | Cover + chapters + data + quotes + examples + CTA |

Each card: 1080×1440 (3:4), consistent design language across the set.
Render: single HTML → full-page Chrome headless → ffmpeg crop into individual PNGs.

**Publishing order in one 小红书 note:**
Posters (P1, P2, ...) → Deep cards (C1, C2, ...) — total <= 18 images (platform limit).

**Output:**
- `content/{name}/deliver/xiaohongshu-publish-kit.md` — complete publish kit with:
  1. Copy-paste post text (title + briefing + tags)
  2. Poster file list with descriptions
  3. Card file list with content summary per card
  4. Recommended publishing order
  5. Complete file manifest with sizes

#### Step 8.3: Confidence Scoring

Calculate the confidence score using this explicit formula. Each item must be
evaluated and the contribution (+/-) shown:

```
confidence_score (1-10):
  +2 if all RP-V checks passed
  +2 if Studio preview was reviewed and approved by user
  +1 if TTS dry-run duration within target range (3-8min B站)
  +1 if all platform specs validated by check_specs.py
  +1 if no REVIEW findings above warning level
  +1 if polyphone pre-flight completed (zh-CN only, +1 if N/A for en-US)
  +1 if all thumbnail sizes generated (16:9, 4:3, 3:4 if applicable)
  +1 if BGM mixed successfully with correct volume level
  -2 if any RP-V check failed and remains unfixed
  -2 if Studio preview was skipped or user did not approve
  -1 if duration outside target range
  -1 per platform spec validation failure
  -1 if brand colors do not match identity.yaml
```

**Show the full breakdown, not just the final number:**
```
Confidence: 9/10
  +2  All 12 RP-V checks passed
  +2  Studio preview approved by user
  +1  Duration 6:42 within 3-8min target
  +1  All platform specs pass (bilibili, youtube)
  +1  Zero REVIEW findings above warning
  +1  Polyphone pre-flight: 12 terms corrected
  +1  Thumbnails: 16:9, 4:3, 3:4 all generated
  +1  BGM mixed at 0.05 volume
  -1  Brand accent color was #4ECDC5 (fixed to #4ECDC4)
```

If confidence < 7 -> flag for human review before publishing.

#### Step 8.4: Generate REPORT.md

Save to `content/{name}/REPORT.md`:

```markdown
# Pollinate Report: {title}

**Run ID:** run_p_{id} | **Date:** {date} | **Confidence:** {score}/10
**Domain:** {domain} | **Formats:** {formats — e.g. Video, Poster, Narrative, Shorts}
**Platforms:** {list}

## 1. Topic Evaluation
| Dimension | Score | Rationale |
|---|---|---|
| Knowledge Differentiation | X/5 | ... |
| Audience Match | X/5 | ... |
| Asset Readiness | X/5 | ... |
| Timeliness | X/5 | ... |
| Production Complexity | X/5 | ... |
| **ROI** | **X.X** | **GO** |

## 2. Content Package
- **Core Thesis:** {one sentence}
- **Key Points:** {count}
- **Differentiation:** {angle}
- **Internal Sources:** {count} files referenced

## 3. Production Summary
| Metric | Value |
|---|---|
| Script length | {chars} chars / {est_duration} |
| TTS engine | {backend} / {voice} |
| Sections | {count} |
| Components used | {list} |
| Thumbnails | {sizes generated} |
| BGM | {track} at {volume} |

## 4. Quality Gates
| Gate | Result |
|---|---|
| RP-V1 Audio sync | ... |
| RP-V2 Safe zone | ... |
| RP-V3 Information density | ... |
| RP-V4 Subtitle accuracy | ... |
| RP-V5 Thumbnail specs | ... |
| RP-V6 Polyphone coverage | ... |
| RP-V7 Resolution & codec | ... |
| RP-V8 Duration target | ... |
| RP-V9 Brand consistency | ... |
| RP-V10 Component variety | ... |
| RP-V11 Text readability | ... |
| RP-V12 Content width | ... |
| Studio preview | ... |
| Platform specs | ... |

## 5. Decision Log
| Stage | Decision | Classification | Reasoning |
|---|---|---|---|
| EVALUATE | ... | mechanical | ... |
| PLAN | ... | taste | ... |
| BUILD | ... | taste | ... |

## 6. Files Produced

List ALL output files by track. Include only tracks that were produced:

### Video Track (if produced)
- `final_video.mp4` -- {resolution}, {duration}, {size}
- `thumbnail_16x9.png` -- 1920x1080
- `thumbnail_4x3.png` -- 1200x900
- `thumbnail_3x4.png` -- 1080x1440 (if applicable)

### Poster Track (if produced)
- `poster-{variant}.png` -- {resolution}, {size}, {description}
- (list each poster with its purpose: cover, matrix, insight, etc.)

### Narrative Track (if produced)
- `card-{N}.png` -- {resolution}, {size}, {section content}
- `cards.html` -- source file for re-rendering
- `xiaohongshu.md` -- plain text version

### Publish Kits
- `deliver/publish_info.md` -- per-platform metadata ({platforms})
- `deliver/xiaohongshu-publish-kit.md` -- 小红书 complete kit (if 小红书 targeted)

## 7. Lessons (from REFLECT)
- ...

## 8. Known Gaps & Attention Flags
- ...

---
Generated by Pollinate | Swarm Content Engine | {date}
```

### Decisions

| Decision | Classification | Default |
|----------|---------------|---------|
| Metadata tone/style per platform | Taste | Per-platform defaults from channels/*.yaml |
| Title formula selection | Taste | B站: number + topic + hook |
| Tag selection | Taste | Mix of broad + specific |
| Confidence score calculation | Mechanical | Formula above |

### Verification Gate

Before advancing to REFLECT, ALL must be true:
- [ ] Delivery Gate completed (taste decisions reviewed or none accumulated)
- [ ] `content/{name}/REPORT.md` saved with all sections filled
- [ ] Confidence breakdown shown (not just final number)
- [ ] `content/{name}/deliver/publish_info.md` exists with per-platform metadata
- [ ] Confidence score >= 7 (or flagged for human review if < 7)
- [ ] All files listed in "Files Produced" section of REPORT.md actually exist

### Anti-Rationalization

| Shortcut | Required Response |
|----------|-------------------|
| "Skip delivery gate, no taste decisions" | Verify there are truly zero. Check every stage's decision log. |
| "Confidence is 10/10, everything is perfect" | Show the breakdown. Confidence without evidence is fiction. |
| "Metadata can be written manually on each platform" | Automated metadata ensures consistency. Generate now. |
| "REPORT.md is boilerplate, skip it" | REPORT.md is the permanent record. Every run produces one. |
| "Only publishing to B站, skip other platform metadata" | Distribution is free. Generate for all target platforms. |

### Max Retries

1. After exhaustion -> checkpoint.

### Output Files

- `content/{name}/REPORT.md` -- full production report
- `content/{name}/deliver/publish_info.md` -- per-platform titles, descriptions, tags, CTAs

### Step 8.5: Auto-Publish to GitHub Pages

After DELIVER verification gate passes, auto-publish to the content gallery:

```bash
SKILL_DIR="$(dirname "$(dirname "$0")")"
python "$SKILL_DIR/scripts/publish_to_pages.py" "content/{name}"
```

This pushes poster HTMLs, PNGs, and narratives to https://xg-gh-25.github.io/swarm-content/
and regenerates the gallery index. Requires Code Defender approval on the repo.

**If push fails** (Code Defender not yet approved, network issue): log a WARNING
and continue to REFLECT. Publishing is non-blocking — content is still in
Knowledge/Pollinate/ locally. Re-run `--all` later to catch up.

---

## Stage 9: REFLECT -- Learn + Improve

### Procedure

1. **Write production lessons** to IMPROVEMENT.md (Video Production section):

   Use this structure:
   ```markdown
   ### Pollinate Run: {topic} ({date})

   **What Worked:**
   - {observation with specific evidence}

   **What Failed:**
   - {observation with specific evidence}

   **Process Insights:**
   - {observation about pipeline efficiency}
   ```

2. **Update user_prefs.json** with learned preferences:
   - Record color/font/speed choices that worked (user approved without changes)
   - Record component combinations that the user kept as-is
   - Per-domain style patterns (e.g., "AI architecture -> dark theme + FlowChart heavy")
   - Add entry to `learning_history` array:
     ```json
     {
       "run_id": "run_p_{id}",
       "topic": "...",
       "domain": "...",
       "learned": ["dark theme works for architecture", "6 sections optimal"],
       "date": "..."
     }
     ```

3. **Log to DailyActivity:**
   - Production record with topic, domain, duration, confidence score
   - Link to REPORT.md

4. **Pattern extraction** (after >= 3 runs):
   - Which domains produce best content?
   - Average production time per domain?
   - Which components get most "keep as-is"?
   - Which sections always need revision? (-> improve templates)

5. **Checklist maintenance** -- if any post-pipeline review or user feedback
   found issues the pipeline missed:
   a. Classify each missed issue: does it fit an existing RP-V pattern?
   b. If yes -> investigate why the pattern check missed it
   c. If no -> propose adding a new RP-V pattern to REVIEW_PATTERNS.md
   d. Document the proposed addition in IMPROVEMENT.md

### Decisions

| Decision | Classification | Default |
|----------|---------------|---------|
| Which patterns to record | Mechanical | Record all observations |
| Style preference updates | Mechanical | Record approved choices |
| New RP-V pattern proposal | Taste | Propose if gap identified |

### Verification Gate

Before marking pipeline COMPLETE, ALL must be true:
- [ ] IMPROVEMENT.md updated with What Worked / What Failed / Process sections
- [ ] `user_prefs.json` has new `learning_history` entry for this run
- [ ] DailyActivity logged with production record
- [ ] All observations are specific (not generic like "went well")

### Anti-Rationalization

| Shortcut | Required Response |
|----------|-------------------|
| "Nothing to learn from this run" | Every run teaches something. Review decision log for patterns. |
| "Preferences haven't changed" | Check if any taste decisions were made. Each one is a potential preference update. |
| "Skip DailyActivity, report is enough" | DailyActivity feeds cross-session learning. Log it. |
| "Pattern extraction needs more runs" | Record observations even before 3 runs. Early data is valuable. |

### Max Retries

1. After exhaustion -> checkpoint.

### Output Files

- Updated IMPROVEMENT.md (appended Video Production section)
- Updated `user_prefs.json` (new learning_history entry)
- DailyActivity log entry

---

## Pipeline Completion

After REFLECT stage, present the completion summary.

**For multi-format runs (poster + narrative + video etc.), the summary must
enumerate every produced asset by track with file sizes:**

```
Pollinate COMPLETE (run_p_{id}) -- {N} stages, {skipped} skipped, {escalations} escalations
Confidence: {score}/10

  Artifacts:
    evaluation    -> evaluation.json (GO, ROI {X.X})
    research      -> research.md (thesis: "{one-liner}")
    strategy      -> PRFAQ.md + strategy.json (channel matrix)
    content_pkg   -> content_package.md ({N} key points)

  Tracks Produced:
    poster/       -> {N} posters (P1: cover {size}, P2: matrix {size}, ...)
    narrative/    -> {N} cards (C1-CN, {total_size})
    video/        -> final_video.mp4 ({resolution}, {duration}, {size}) [if produced]
    shorts/       -> {N} clips [if produced]

  Publish Kits:
    小红书         -> xiaohongshu-publish-kit.md (title + briefing + {P} posters + {C} cards)
    B站           -> publish_info.md [if video produced]
    YouTube       -> publish_info.md [if video produced]

  Quality: {N}/12 RP-V checks passed [or N/A if non-video run]
  Decisions: {X} mechanical, {Y} taste (all approved), {Z} judgment
  Lessons: {N} written to IMPROVEMENT.md

  Report: content/{name}/REPORT.md
```

**小红书-only runs (poster + narrative, no video):** RP-V checks that only apply
to video (RP-V1 audio sync, RP-V7 resolution/codec, RP-V8 duration) are N/A.
The confidence formula adapts: video-specific items score +1 each as N/A (neutral).

---

## Rules

1. **Execute inline, never invoke skills.** You ARE the pipeline. Run each
   stage's behavior directly. Do not use `/evaluate` or `/qa` as slash commands.

2. **Studio preview is MANDATORY.** NEVER render 4K until user explicitly
   confirms in the Studio preview. This gate cannot be bypassed by any
   automation, any shortcut, or any rationalization. The user says "render 4K"
   or the pipeline does not advance.

3. **Classify every decision.** No unclassified decisions. If unsure, default
   to "taste" (surface at delivery gate rather than block or ignore).

4. **Save timing before concat.** timing.json + SRT must be saved before
   audio concatenation. If the concat step fails, timing data would be lost
   without this safeguard.

5. **Copy patterns, don't simplify.** When using code from video-podcast-maker
   or templates, copy the ENTIRE pattern including edge case handling. Do not
   "simplify" by removing error handling or fallback paths. See design doc
   Section 9 for migration rules.

6. **Never loop forever.** Respect max_retries per stage. After exhaustion,
   checkpoint. Three attempts at the same stage is enough.

7. **Taste decisions batch at delivery.** Don't interrupt the user mid-pipeline
   for taste decisions. Accumulate them, present once at the Delivery Gate.

8. **Always generate REPORT.md.** Every pipeline run produces a markdown
   report at `content/{name}/REPORT.md`. This is the permanent record.

9. **Brand consistency is non-negotiable.** Apply `brand/identity.yaml`
   colors, fonts, and voice configuration in every output. Swarm Orange is
   #FF6B35, not #FF6C36, not "close enough." PingFang SC, not a substitute.

10. **External content principles are non-negotiable.** For any externally-facing
    content (social media, posters, demos, pitches), apply `brand/content_principles.md`
    anti-pattern checklist at the QUALITY stage. Any violation → fix before delivery.
    Key rules: no output metrics as value (P1), no first-person hero framing (P2),
    thesis-driven not feature-driven (P3), effects over mechanisms (P4).

11. **Platform specs are non-negotiable.** `check_specs.py` must pass for
    every target platform before delivery. No exceptions, no "mostly passes,"
    no manual overrides.

---

## Process Enforcement Gates (G1-G6)

_Added 2026-05-03 from user-perspective pipeline audit._

### G1: Studio Preview Gate Enforcement

The Studio preview gate at BUILD Stage 5 is the single most important quality
control in the pipeline. It has NO enforcement mechanism beyond agent honesty.

**Explicit enforcement rules:**

1. Agent MUST output a visible preview: either open the browser via `npx remotion
   preview` or output an inline image `![preview](path/to/screenshot.png)`.
2. User approval MUST use one of these exact tokens: `"approved"`, `"looks good"`,
   `"render 4K"`, `"render final"`, `"proceed"`.
3. **Anti-rationalization:** If you catch yourself about to type _"the preview looks
   correct based on the composition data"_ or _"the timing.json structure validates
   correctly"_ — **STOP.** That's data review, not visual review. You haven't
   previewed. Open the browser.
4. No auto-proceed after timeout. No implicit approval. No "user didn't object."

### G2: RP-V Checklist Automation

Run `python scripts/check_rpv.py <content_dir>` at REVIEW stage. The script
auto-checks 6 patterns from data files (timing.json, SRT, composition):

- RP-V1: Audio-video sync (timing.json vs SRT alignment)
- RP-V3: Information density (sections × key points)
- RP-V4: Subtitle accuracy (SRT entry count vs script sections)
- RP-V5: Thumbnail sizes (all required aspects present)
- RP-V8: Duration target (within platform range)
- RP-V11: Text sizes (from composition data)

Remaining 6 (RP-V2, V6, V7, V9, V10, V12) require human judgment — agent must
explicitly list each with PASS/FAIL/N-A and evidence.

### G3: Git Commit Strategy

Commit at the end of each pipeline stage. Message format:
```
content(pollinate): {topic-slug} — stage {N} {STAGE_NAME}
```
REFLECT stage does the final commit including REPORT.md.

### G4: Delivery Gate Override Cascade

When user overrides a taste decision at the Delivery Gate, re-run the minimum
set of affected stages:

| Decision made in | Override re-runs |
|------------------|-----------------|
| EVALUATE | EVALUATE + all downstream |
| THINK | THINK + STRATEGIZE + PLAN + BUILD |
| STRATEGIZE | STRATEGIZE + PLAN + BUILD |
| PLAN | PLAN + BUILD |
| BUILD | BUILD only |
| REVIEW | REVIEW only (re-check) |

Never re-run EVALUATE or THINK for taste overrides in BUILD or later stages.

### G5: REFLECT Observation Schema

Each observation in REFLECT MUST follow this structure:
```json
{
  "stage": "build",
  "category": "worked|failed|process",
  "observation": "Specific factual observation",
  "action_taken": "What was done about it (or 'logged for future')",
  "reusable": true
}
```
After ≥3 runs, observations with `"reusable": true` promote to `user_prefs.json`
as learned preferences.

### G6: Context Budget Tracking

After each stage completes, output:
```
Stage {N} complete. Context: ~{X}% estimated.
```
If >60%, checkpoint. If >80%, compress content_package to thesis + key_points only
and resume in new session with truncated context.
