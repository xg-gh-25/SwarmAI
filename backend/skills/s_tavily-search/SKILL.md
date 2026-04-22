---
name: Tavily Search
description: >
  AI-powered web search and content extraction via Tavily API. Search the web, get AI answers, extract content from URLs.
  TRIGGER: "search the web", "web search", "tavily", "find online", "search for", "latest news about", "what's happening with".
  DO NOT USE: for fetching a single known URL (use WebFetch), GitHub repo research (use github-research), or deep multi-round investigation (use deep-research).
  SIBLINGS: deep-research = multi-round investigation with synthesis | summarize = condense known content | tavily-search = fast web search + extraction as a building block.
tier: always
---
# Tavily Search

**Why?** WebFetch can only grab a single known URL. Tavily searches the entire web, returns ranked results with relevance scores, and can generate AI-powered answers -- all in one API call. Essential for answering "what's the latest on X?" or powering research workflows.

---

## Quick Start

```
"Search the web for latest AI agent frameworks"
"What's the latest news about OpenAI?"
"Extract the main content from these 5 URLs"
```

---

## Setup

Requires `TAVILY_API_KEY` environment variable.

```bash
# Check if set
echo $TAVILY_API_KEY

# If not, set it (get key from https://app.tavily.com)
export TAVILY_API_KEY=tvly-your-key-here
```

Free tier: 1,000 API credits/month. Basic search = 1 credit. Advanced search = 2 credits.

---

## API Reference

### Search

**POST** `https://api.tavily.com/search`

```bash
curl -s -X POST https://api.tavily.com/search \
  -H "Authorization: Bearer $TAVILY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "latest AI agent frameworks 2026",
    "search_depth": "basic",
    "max_results": 5,
    "include_answer": "basic"
  }'
```

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `query` | string | (required) | Search query |
| `search_depth` | string | `basic` | `basic` (fast, 1 credit) or `advanced` (thorough, 2 credits) |
| `max_results` | int | 5 | Number of results (0-20) |
| `include_answer` | bool/string | false | `true`, `"basic"`, or `"advanced"` -- returns AI-generated answer |
| `include_raw_content` | bool/string | false | `true`, `"markdown"`, or `"text"` -- returns full page content |
| `topic` | string | `general` | `"general"` or `"news"` |
| `time_range` | string | none | `"day"`, `"week"`, `"month"`, `"year"` |
| `country` | string | none | ISO country code to boost results (e.g., `"us"`, `"tw"`, `"cn"`) |
| `include_images` | bool | false | Return related images |
| `exclude_domains` | array | none | Domains to exclude (e.g., `["pinterest.com"]`) |
| `include_domains` | array | none | Limit search to specific domains |

**Response:**

```json
{
  "query": "latest AI agent frameworks 2026",
  "answer": "AI-generated summary of search results...",
  "results": [
    {
      "title": "Page Title",
      "url": "https://example.com/article",
      "content": "Relevant snippet from the page...",
      "score": 0.95,
      "published_date": "2026-03-07"
    }
  ],
  "response_time": 1.23
}
```

### Extract

**POST** `https://api.tavily.com/extract`

```bash
curl -s -X POST https://api.tavily.com/extract \
  -H "Authorization: Bearer $TAVILY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "urls": [
      "https://example.com/article1",
      "https://example.com/article2"
    ],
    "format": "markdown"
  }'
```

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `urls` | string/array | (required) | Single URL or up to 20 URLs |
| `query` | string | none | Reranks extracted chunks by relevance |
| `chunks_per_source` | int | 3 | Chunks per URL (1-5, requires `query`) |
| `extract_depth` | string | `basic` | `"basic"` (1 credit/5 URLs) or `"advanced"` (2 credits/5 URLs) |
| `format` | string | `markdown` | `"markdown"` or `"text"` |
| `include_images` | bool | false | Include image URLs from pages |

**Response:**

```json
{
  "results": [
    {
      "url": "https://example.com/article1",
      "raw_content": "# Article Title\n\nFull markdown content..."
    }
  ],
  "failed_results": [],
  "response_time": 2.1
}
```

---

## Workflow

### Step 1: Determine Search Type

| User Request | Search Configuration |
|---|---|
| General question | `search_depth: "basic"`, `include_answer: "basic"` |
| Current events / news | `topic: "news"`, `time_range: "week"` |
| Thorough research | `search_depth: "advanced"`, `max_results: 10` |
| Region-specific | Add `country` parameter |
| Domain-specific | Use `include_domains` |
| Competitive analysis | Multiple searches + extract |

### Step 2: Execute Search

```bash
curl -s -X POST https://api.tavily.com/search \
  -H "Authorization: Bearer $TAVILY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "user query here",
    "search_depth": "basic",
    "max_results": 5,
    "include_answer": "basic"
  }'
```

### Step 3: Present Results

Format results clearly:

```
## Search Results: [query]

**AI Answer:** [Tavily's generated answer if requested]

**Sources:**
1. [Title](url) (relevance: 95%)
   Key snippet from the page...

2. [Title](url) (relevance: 88%)
   Key snippet from the page...

3. ...
```

### Step 4: Offer Follow-ups

After presenting results:
- "Want me to extract the full content from any of these?"
- "Should I do a deeper search?"
- "Want me to summarize the top results?"

---

## Common Patterns

### Quick Answer

When user just wants a fast answer:

```bash
curl -s -X POST https://api.tavily.com/search \
  -H "Authorization: Bearer $TAVILY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "what is the current population of Taiwan",
    "include_answer": "advanced",
    "max_results": 3
  }'
```

Present the `answer` field directly, with sources listed below.

### News Check

```bash
curl -s -X POST https://api.tavily.com/search \
  -H "Authorization: Bearer $TAVILY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "OpenAI latest announcements",
    "topic": "news",
    "time_range": "week",
    "max_results": 5,
    "include_answer": "basic"
  }'
```

### Batch Content Extraction

When you have multiple URLs and need their content:

```bash
curl -s -X POST https://api.tavily.com/extract \
  -H "Authorization: Bearer $TAVILY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "urls": [
      "https://article1.com",
      "https://article2.com",
      "https://article3.com"
    ],
    "format": "markdown",
    "query": "focus topic for relevance ranking"
  }'
```

### Search + Extract Pipeline

For thorough research on a topic:

1. **Search** to find relevant URLs
2. **Extract** full content from top results
3. **Summarize** (use s_summarize) or analyze

```bash
# Step 1: Search
RESULTS=$(curl -s -X POST https://api.tavily.com/search \
  -H "Authorization: Bearer $TAVILY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query": "topic", "max_results": 5}')

# Step 2: Extract top URLs (parse from results)
curl -s -X POST https://api.tavily.com/extract \
  -H "Authorization: Bearer $TAVILY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"urls": ["url1", "url2", "url3"], "format": "markdown"}'

# Step 3: Summarize extracted content
```

### Powering Other Skills

Tavily is a building block for other skills:

| Skill | How Tavily Helps |
|---|---|
| s_deep-research | Primary search engine for multi-round research |
| s_summarize | Fetch content from URLs before summarizing |
| s_consulting-report | Market data, competitor info, industry news |
| s_podcast-gen | Research topics before scripting |

---

## Cost Management

| Operation | Credits |
|---|---|
| Basic search | 1 |
| Advanced search | 2 |
| Basic extract (per 5 URLs) | 1 |
| Advanced extract (per 5 URLs) | 2 |

**Free tier:** 1,000 credits/month

**Tips to conserve credits:**
- Use `basic` search depth for most queries (1 credit vs 2)
- Use `max_results: 3-5` unless you need more
- Batch URL extractions (5 URLs = 1 credit vs 5 separate WebFetch calls)
- Use `include_answer` to get a quick answer without extracting full pages
- Don't set `include_raw_content: true` on search unless you need full page text

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "TAVILY_API_KEY not set" | Get key from https://app.tavily.com, then `export TAVILY_API_KEY=tvly-...` |
| 401 Unauthorized | API key invalid or expired. Check at app.tavily.com |
| 429 Rate limited | Too many requests. Wait and retry |
| 432 Credit limit | Monthly credits exhausted. Upgrade plan or wait for reset |
| Empty results | Try broader query, remove domain restrictions, check spelling |
| Extract fails for URL | Site may block bots. Try `extract_depth: "advanced"` |
| Slow response | Use `search_depth: "basic"` or reduce `max_results` |
| Results not recent enough | Add `time_range: "day"` or `"week"` |

---

## Quality Rules

- Always check `TAVILY_API_KEY` is set before making calls
- Present AI-generated answers with source attribution
- Show relevance scores to help user gauge result quality
- For news queries, always include published dates
- Don't over-fetch: use minimal `max_results` needed
- When used as building block for other skills, keep credit usage efficient
- Never expose the API key in output or messages
- Offer to go deeper (extract, advanced search) after initial results
- For Chinese queries, consider adding `country: "tw"` or `"cn"` for better regional results

## Verification

Before marking this task complete, show evidence for each:

- [ ] **Search query shown** — the exact query string sent to Tavily API is displayed (not just the user's natural-language request)
- [ ] **Results returned with URLs** — each result includes a title, URL, relevance score, and content snippet
- [ ] **Relevance confirmed** — results are on-topic for the user's intent; off-topic or low-score results are filtered or flagged
- [ ] **API key validated** — `TAVILY_API_KEY` was confirmed set before the call was made (no auth errors)
- [ ] **Credit-efficient** — search used appropriate depth (basic vs advanced) and minimal `max_results` for the task
