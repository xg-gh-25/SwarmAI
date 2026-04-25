---
name: Learn Content
description: >
  Ingest URLs, articles, or text from any source into structured knowledge cards.
  Stores source URL + metadata + briefing (NOT full text). On-demand retrieval from source.
  TRIGGER: "learn this", "save this article", "read and remember", "ingest this", "learn from".
  DO NOT USE: for deep multi-source research (use deep-research), quick summaries without storage (use summarize), or saving raw facts to memory (use save-memory).
  SIBLINGS: summarize = quick summary, no storage | deep-research = multi-source investigation | save-memory = raw facts to MEMORY.md | learn-content = structured knowledge card with source retrieval.
tier: always
---
# Learn Content

Ingest content from any source into a structured **knowledge card** — a lightweight index entry that stores the source, key insights, and tags. NOT full-text archival. The source URL is the single source of truth; the card is an index pointer with enough context to decide whether to re-read.

## Storage

```
~/.swarm-ai/SwarmWS/Knowledge/Learned/YYYY-MM-DD-<slug>.md
```

One file per ingested item. No hard size limit — prioritize learning value over compactness.

## Workflow

### Step 1: Accept Input

User provides one or more of:
- URL (article, blog post, tweet thread, GitHub repo, WeChat article)
- Pasted text block
- File path (PDF, doc, etc.)
- Forwarded message with link

Detect the input type:

| Input | Action |
|-------|--------|
| URL (general) | **3-tier fetch chain** (see below) |
| Text block (no URL) | Use directly — store as `source_type: text` |
| File path (video/audio) | ffmpeg extract audio → whisper-transcribe → text |
| File path (document) | Read tool / appropriate skill (s_pdf, s_docx) |
| Multiple URLs | Process each separately, one card per URL |

### 3-Tier Fetch Chain (BLOCKING — exhaust all tiers before asking user)

Every URL goes through this chain. Stop at the first tier that returns usable content.

**Tier 1: WebFetch** (fastest, works for ~70% of URLs)
- Standard fetch. If it returns real content, done.
- Skip to Tier 2 if: anti-scraping block, "环境异常", empty body, login wall, SPA shell (`<div id="app"></div>`)

**Tier 2: curl with platform-specific UA** (works for ~20% more)
```bash
# WeChat articles (mp.weixin.qq.com)
curl -sL -H "User-Agent: Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.43" "<URL>"

# General anti-scraping (Douyin pages, news sites)
curl -sL -A "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36" -H "Accept-Language: zh-CN,zh;q=0.9" "<URL>"
```
- WeChat: extract from `og:` tags + `id="js_content"` div
- General: extract from `<article>`, `<main>`, or largest text block
- Skip to Tier 3 if: SPA with no server-rendered content, JS-only rendering, video page

**Tier 3: browser-agent** (JS-rendered content, video metadata, SPA pages)
```bash
node .claude/skills/s_browser-agent/browser-agent.mjs launch     # if not running
node .claude/skills/s_browser-agent/browser-agent.mjs navigate "<URL>"
node .claude/skills/s_browser-agent/browser-agent.mjs read        # get rendered DOM
node .claude/skills/s_browser-agent/browser-agent.mjs screenshot /tmp/page.png  # visual context
```
- Gets JS-rendered content that curl/WebFetch can't see
- Works for: WeChat 视频号, 抖音 share pages, SPA news sites, any JS-heavy page
- Extracts: title, author, description, metadata, visible text
- For video pages: gets title + description + comments (NOT the video stream itself)
- Screenshot provides visual context for understanding page layout

**After all 3 tiers:** If still no usable content (login-gated, expired, region-blocked):
1. State what was tried and why each tier failed
2. Ask: "Can you paste the key content or share the video file directly?"

### Video & Audio URL Handling

When the URL points to a video/audio (detected from URL pattern or page metadata):

| Platform | What browser-agent extracts | What needs user help |
|----------|---------------------------|---------------------|
| WeChat 视频号 (`weixin.qq.com/sph/`) | Title, author, description, tags, engagement stats | Video file (for full transcript) |
| 抖音 share page | Title, author, description, hashtags | Video file |
| B站 / YouTube | Title, author, description, comments (public) | Usually nothing — yt-dlp can download |
| TikTok | Title, author, hashtags | Video file |
| Podcast / audio URL | Direct download if public | Nothing |

**For B站 and YouTube:** Try `yt-dlp` to download audio before asking user:
```bash
yt-dlp -x --audio-format mp3 -o /tmp/video_audio.mp3 "<URL>" 2>&1
```
If download succeeds → whisper-transcribe → full transcript.

**For WeChat/抖音/TikTok:** These block download tools. Create the card from metadata (title, description, tags, engagement) and note in the card that full transcript requires the video file.

### Source Type Detection

| URL Pattern | source_type |
|-------------|-------------|
| `mp.weixin.qq.com` | `wechat` |
| `weixin.qq.com/sph/` or `channels.weixin.qq.com` | `wechat-video` |
| `douyin.com`, `v.douyin.com` | `douyin-video` |
| `bilibili.com`, `b23.tv` | `bilibili-video` |
| `youtube.com`, `youtu.be` | `youtube-video` |
| `*.tiktok.com` | `tiktok-video` |
| `twitter.com`, `x.com` | `tweet` |
| `github.com` | `repo` |
| Everything else | `article` |

### Step 2: Extract & Classify

From the fetched content, extract:

| Field | Required | Description |
|-------|----------|-------------|
| title | Yes | Article/content title |
| source_url | Yes (if URL) | Original URL for on-demand retrieval |
| source_type | Yes | `article`, `paper`, `tweet`, `repo`, `video`, `text`, `wechat` |
| author | If available | Author name or handle |
| date_published | If available | Original publish date |
| domain | Yes | What domain does this relate to? |
| tags | Yes | 3-7 keyword tags for search |
| briefing | Yes | Structured learning notes — reader should get 80% of article value without reading the original (see Briefing section below) |
| connections | Yes | Cross-references to existing knowledge (see Step 2b) |
| relevance | If obvious | How this connects to user's work (SwarmAI, AIDLC, AWS, etc.) |
| quote | Optional | 1-2 verbatim quotes that capture the essence |

### Step 2b: Cross-Reference Existing Knowledge

Before writing the card, check for connections:

1. **MEMORY.md** — scan Key Decisions and Lessons Learned for related entries. If the new content validates, contradicts, or extends an existing entry, note it. Use the `[KDxx]`, `[LLxx]` keys.
2. **Existing Learned cards** — glob `Knowledge/Learned/` for related tags/domains. If this is a follow-up or counterpoint to a previous card, link it.
3. **DDD docs** — if the content relates to an active project (SwarmAI, AIDLC), note the connection.

Write connections in the Relevance section: `Validates KD06 (memory sovereignty). Extends 2026-04-18-memory-is-the-moat.md.`

**Why this matters:** Isolated cards = information hoarding. Connected cards = compounding knowledge. The cross-reference is what makes "learn" different from "bookmark."

### Step 3: Determine Domain

Classify into one of these domains based on content:

| Domain | Examples |
|--------|---------|
| `ai-agents` | Agent architectures, memory systems, orchestration, tools |
| `ai-models` | New model releases, benchmarks, capabilities |
| `ai-products` | Products, startups, competitive landscape |
| `engineering` | Software engineering, architecture, DevOps, testing |
| `management` | Leadership, team dynamics, decision-making |
| `aws` | AWS services, features, competitive positioning |
| `industry` | Market trends, business models, strategy |
| `personal` | Health, finance, lifestyle, misc |

If content spans multiple domains, pick the primary one and add others as tags.

### Step 4: Write Knowledge Card

Create file at `Knowledge/Learned/YYYY-MM-DD-<slug>.md`:

```markdown
---
title: "<title>"
source_url: "<url>"
source_type: <article|paper|tweet|repo|video|text|wechat>
author: "<author>"
date_published: "<YYYY-MM-DD or unknown>"
date_ingested: "<YYYY-MM-DD>"
domain: "<domain>"
tags: [tag1, tag2, tag3]
---

## TL;DR

<2-3 sentences. The one takeaway if you read nothing else.>

## Key Insights

For each major insight from the article, write a mini-section:

### <Insight title — a prescriptive statement, not a topic label>

<2-5 sentences explaining the insight with enough context to understand WHY it matters, not just WHAT was said. Include specific examples, numbers, or frameworks from the source. The reader should think "I understand this well enough to explain it to someone else.">

Repeat for each major insight (typically 4-8 per article).

## Frameworks & Mental Models

<If the article introduces a framework, taxonomy, decision matrix, or mental model — capture it here with enough structure to reuse. Skip this section if the article doesn't introduce one.>

## Relevance

<How this connects to current work, existing knowledge, and decisions. Reference MEMORY.md keys [KDxx], [LLxx] and existing Learned cards where applicable.>

## Quotes

> "Verbatim quote" — Author
> "Another quote" — Author

## Source

URL: <source_url>
To deep-read: `learn more about "<title>"` or fetch the URL directly.
```

**Depth calibration:** The goal is that someone reading ONLY this card gets ~80% of the article's value. If the original is a 30-minute read, the card should be a 5-minute read. If the original is a 3-minute read, the card can be shorter. Scale depth to source depth.

### Step 5: Update KNOWLEDGE.md Index

Add the new card to `KNOWLEDGE.md` under the `### Learned` section:

```
| YYYY-MM-DD | `Learned/YYYY-MM-DD-<slug>.md` | <title> — <domain> |
```

This keeps the index scannable without opening individual files.

### Step 6: Confirm

Output to user:

```
Learned: <title>
Domain: <domain> | Tags: <tags>
Briefing: <first 2-3 bullet points>
Stored: Knowledge/Learned/YYYY-MM-DD-<slug>.md
```

Keep confirmation compact — 4-5 lines max. Don't repeat the full card.

## On-Demand Retrieval

When the user asks about previously learned content:

- "What did I learn about memory systems?" → Search `Knowledge/Learned/` by tags/domain
- "Tell me more about that article on agent harnesses" → Find the card, WebFetch the source_url, provide detailed summary
- "What have I learned this week?" → List recent cards with briefings

### Search Flow

1. Glob `Knowledge/Learned/*.md`
2. Match by: filename pattern, YAML frontmatter tags, domain, title keywords
3. Return matching cards with briefings
4. If user wants depth → WebFetch the source_url and do a full summarize

## Batch Mode

If user sends multiple URLs at once:
1. Process each URL sequentially
2. One card per URL
3. Show a summary table at the end:

```
| # | Title | Domain | Tags |
|---|-------|--------|------|
| 1 | ... | ai-agents | memory, harness |
| 2 | ... | engineering | testing, TDD |
```

## Rules

- **Learning notes, not full text** — don't copy-paste the article. Distill, restructure, and add context. But make it rich enough that the reader learns from the card itself.
- **Briefing = insights, not summary** — each bullet should be a takeaway worth remembering, not a description of what the article says. Self-check each bullet with the **"so what?" test**: if someone reads only this bullet with zero context, do they learn something actionable? If the bullet is a definition or fact restatement, rewrite it as a prescription or implication.
  - BAD: "Entangled agentic systems = software that adapts to user behavior" (definition)
  - GOOD: "Build software that becomes harder to leave the more you use it — that's the only durable moat" (prescription)
  - BAD: "Each generation of tooling commoditizes faster" (observation)
  - GOOD: "Don't differentiate on infra — every tooling layer commoditizes within 18 months" (actionable)
- **Tags for retrieval** — pick tags you'd actually search for later. Prefer specific (`agent-memory`, `oom-fix`) over generic (`ai`, `tech`).
- **Slug from title** — lowercase, hyphens, max 50 chars. `2026-04-25-memory-is-the-moat.md`
- **One card per source** — don't merge multiple articles into one card.
- **Idempotent** — if the same URL is submitted again, update the existing card instead of creating a duplicate. Match by `source_url` in frontmatter.
- **Language match** — write the briefing in the same language as the source content. Chinese article = Chinese briefing.

## Edge Cases

| Situation | Handling |
|-----------|----------|
| URL returns 404 / paywall | 3-tier chain first. Only ask user after all 3 fail. |
| Video URL (WeChat/抖音/TikTok) | browser-agent extracts metadata. Card created from title+description+tags. Note: "full transcript requires video file." |
| Video URL (YouTube/B站) | yt-dlp download → whisper-transcribe → full transcript card |
| Video file (user drops .mp4/.mov) | ffmpeg extract audio → whisper-transcribe → full transcript card |
| SPA / JS-rendered page | Tier 1+2 fail → browser-agent renders JS → extracts DOM text |
| Tweet / short post | Inline the full text as a quote — too short for briefing bullets |
| GitHub repo | Focus on: what it does, architecture, why it matters. Use README as source |
| PDF / long paper | Extract abstract + conclusions, skip methodology details |
| Duplicate URL | Update existing card, note update date |
| User provides only text, no URL | `source_type: text`, no `source_url`, store the text as a quote block |

## Verification

Before marking this task complete, show evidence for each:

- [ ] **Source fetched** — content was successfully retrieved (or user provided it)
- [ ] **Card created** — file exists at `Knowledge/Learned/YYYY-MM-DD-<slug>.md` with valid YAML frontmatter
- [ ] **Briefing is insights, not summary** — each bullet is a takeaway, not a description
- [ ] **Tags are searchable** — 3-7 specific, useful tags
- [ ] **Learning value** — reader gets ~80% of article value from the card alone, without reading the original
