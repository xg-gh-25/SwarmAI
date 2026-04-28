# GitHub Trending — Full Workflow

## Interactive Use

When the user asks about GitHub Trending ("github trending", "what's hot on github", etc.):

### Step 1: Fetch

Use WebFetch to get the GitHub Trending page:

```
WebFetch(url="https://github.com/trending", prompt="List ALL trending repositories. For each: repo name (owner/name), description, language, stars today, total stars. Numbered list.")
```

For language-specific trending:
```
WebFetch(url="https://github.com/trending/python", prompt="...")
```

### Step 2: Analyze

Present results as a formatted table:

```markdown
## GitHub Trending — YYYY-MM-DD

| # | Repo | Description | Language | Stars Today | Total |
|---|------|-------------|----------|-------------|-------|
| 1 | owner/name | desc | Python | +1,234 | 45.6K |
```

### Step 3: Classify Relevance

Tag each repo by relevance to SwarmAI interests:

| Tag | Criteria |
|-----|----------|
| **agent** | AI agent, coding agent, agent framework |
| **memory** | Memory for AI, context management, recall |
| **voice** | Voice AI, TTS, STT, speech |
| **devtools** | Developer tools, CLI, IDE extensions |
| **model** | LLM, model release, training |
| **infra** | Cloud, deployment, infrastructure |

### Step 4: Trend Commentary

Add 2-3 bullet observations about patterns:
- Which ecosystems are surging?
- Any repos relevant to current SwarmAI work?
- Competitive signals?

## Daily Job Integration

The adapter (`backend/jobs/adapters/github_trending.py`) runs automatically via
the signal pipeline. Results flow:

```
github_trending adapter → signal_fetch → dedup → signal_digest → signal_digest.json
  → Welcome Screen briefing (signals section)
  → Slack channel (if configured)
```

Feed config in `Services/swarm-jobs/config.yaml`:
```yaml
- id: github-trending
  name: GitHub Trending
  type: github-trending
  tier: engineering
  config:
    spoken_language: ''  # '' = all languages
    since: daily         # daily | weekly | monthly
    top_n: 25
```

No manual intervention needed — the daily signal pipeline handles everything.
