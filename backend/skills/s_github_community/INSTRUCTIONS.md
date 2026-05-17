# GitHub Community Engine — INSTRUCTIONS

## Overview

A 7-stage autonomous flywheel: MONITOR → MATCH → DRAFT → PUBLISH → TRACK → CULTIVATE → REPORT

**XG's role:** Weekly report review (2 min). Occasional spot-check. Strategic direction.
**Engine's role:** Everything else — fully autonomous within guardrails.

## Invocation Modes

| Mode | Trigger | What happens |
|------|---------|-------------|
| **Full cycle** | `/github-community` or scheduled job | Run all 7 stages sequentially |
| **Monitor only** | `/github-community scan` | MONITOR + MATCH → show opportunities |
| **Track only** | `/github-community track` | TRACK → show reply status |
| **Report only** | `/github-community report` | Generate weekly HTML report |
| **Engage** | `/github-community engage <repo>#<issue>` | DRAFT + PUBLISH for specific target |

## Stage 1: MONITOR

Scan Source Matrix repos for new signals.

```bash
cd backend && .venv/bin/python -m skills.s_github_community.scripts.monitor --dry-run
```

**Process:**
1. For each Tier 1 repo: fetch issues updated in last 12h (2x/day = 12h window)
2. For each Tier 2 repo: fetch issues updated in last 24h
3. Match issue titles against Topic Matrix keywords
4. Output: `signals.json` with ranked raw signals

**Source Matrix is in:** `Projects/GitHub_Community/TECH.md` (Tier 1-3 repos)
**Topic keywords are in:** `scripts/monitor.py` TOPIC_KEYWORDS dict

## Stage 2: MATCH

Score signals and rank opportunities.

```bash
cd backend && .venv/bin/python -c "
from skills.s_github_community.scripts.match import score_opportunity, Signal
# Score each signal from monitor output
"
```

**Scoring formula** (from TECH.md):
```
score = topic_relevance × expertise_depth × audience_reach
      + first_responder_bonus(5)
      + reply_to_us_bonus(50)
      + maintainer_issue_bonus(3)
      - staleness_penalty(2/day)
      - anti_spam_gate(999 if >2/week on same repo)
```

**Threshold:** score >= 30 → proceed to DRAFT

**DDD-informed modifiers:**
- Read IMPROVEMENT.md → if "this topic×repo never gets replies" → score × 0.5
- Read PRODUCT.md → if topic not in allowlist → reject entirely

## Stage 3: DRAFT

Generate comment text with confidence score.

**Process:**
1. Read the full issue/discussion (not just title)
2. Read TECH.md → find our relevant architecture/pattern for this topic
3. Read IMPROVEMENT.md → what tone/format works for this repo?
4. Read PRODUCT.md → confirm topic is in allowlist

**Comment structure:**
- **Hook:** Acknowledge their specific problem (prove we read it)
- **Body:** Our concrete experience (code, data, or architecture)
- **Value:** Actionable suggestion they can use immediately
- **Bridge:** Max 1 natural cross-link (only if genuinely helpful, at end)

**Confidence scoring (1-10):**
- Did we actually solve this exact problem? (+3)
- Do we have code/data to show? (+2)
- Is our approach different from existing comments? (+2)
- Does IMPROVEMENT.md say this format works here? (+1)
- Is this a stretch / tangential? (-3)

## Stage 4: PUBLISH (Quality Gate)

**BLOCKING — 4 conditions ALL must pass:**

1. ✅ First-hand experience with this exact problem
2. ✅ Offers something existing comments don't
3. ✅ Includes: code snippet, measured data, architecture insight, or production lesson
4. ✅ Would not be embarrassed if maintainer asks "show me the code"

**Publish logic:**
```
confidence >= 8 → Auto-publish via gh api
confidence 5-7 → Hold for weekly report (XG reviews)
confidence < 5 → Discard (log insight to TECH.md silently)
```

**To publish:**
```bash
gh api repos/<owner>/<repo>/issues/<number>/comments -X POST -f body="<comment>"
```

**After publish:** Log to `engagement_log.jsonl`:
```json
{"repo": "...", "issue_number": N, "comment_id": N, "confidence": N,
 "topic": "T-XXX", "posted_at": "...", "status": "active"}
```

## Stage 5: TRACK

Check replies on all active threads.

```bash
cd backend && .venv/bin/python -m skills.s_github_community.scripts.track --dry-run
```

**Process:**
1. Load active threads from `engagement_log.jsonl`
2. For each: check for new comments after ours via `gh api`
3. Score engagement: 0=ignored, 1=upvoted, 2=replied, 3=maintainer responded
4. Log replies to `reply_archive.jsonl`

**Also track:** swarm-content Discussions (inbound comments)
```bash
gh api graphql -f query='{ repository(owner:"xg-gh-25", name:"swarm-content") {
  discussions(first:20) { nodes { number comments { totalCount } } }
}}'
```

## Stage 6: CULTIVATE

Update DDD from engagement data. **This is the flywheel's power source.**

```bash
cd backend && .venv/bin/python -m skills.s_github_community.scripts.cultivate --dry-run
```

**What gets updated:**

| DDD Doc | What feeds it | Example |
|---------|--------------|---------|
| PRODUCT.md | Topic temperature changes | "T-MvS engagement 4x → raise to 🔥🔥🔥" |
| TECH.md | New patterns from replies, new repo discoveries | "hermes maintainer confirmed sort approach" |
| IMPROVEMENT.md | What worked/failed, engagement patterns | "Controversy + data format → highest reply rate" |
| PROJECT.md | Active thread updates, weekly stats | "12 threads active, 3 replies received" |

**Also feeds THESIS.md** (workspace-level):
- Counter-argument from maintainer → add as "Challenge" to thesis
- Validation → add as evidence
- Novel pattern → candidate for new thesis?

## Stage 7: REPORT

Generate 6-tab HTML weekly report.

```bash
cd backend && .venv/bin/python -m skills.s_github_community.scripts.report --dry-run
```

**Tabs:**
1. Source Matrix — all repos, tiers, engagement scores, reply rates
2. Topic Matrix — temperatures, status, best-performing repo per topic
3. Activity — comments posted, replies received, quality scores
4. Learnings — new patterns, THESIS updates, DDD changes (with source attribution)
5. DDD Health — doc freshness, matrix sizes, patterns count
6. Actions — pending drafts, replies to respond to, new repos to consider

**Output:** HTML file in `Projects/GitHub_Community/.artifacts/retro_weekly/`

## Scheduling

| Job | Schedule | What runs |
|-----|----------|-----------|
| Morning scan | 10:00 Beijing (Mon-Fri) | MONITOR + MATCH |
| Evening scan | 18:00 Beijing (Mon-Fri) | MONITOR + MATCH + TRACK |
| Saturday scan | 10:00 Beijing | MONITOR + MATCH + TRACK |
| Weekly report | Sunday 20:00 Beijing | TRACK + CULTIVATE + REPORT |

## Guardrails

- Max 2-3 comments per repo per week (anti-spam)
- Never auto-publish confidence < 8
- Never engage on topics outside PRODUCT.md allowlist
- Never reveal internal paths or private code
- Backoff: if downvoted/hidden → pause that repo 2 weeks
- Kill switch: XG says "stop" → engine pauses all activity

## Records (never delete)

```
Projects/GitHub_Community/.artifacts/
├── engagement_log.jsonl    — every comment posted
├── reply_archive.jsonl     — every reply received
├── quality_scores.jsonl    — per-comment quality over time
├── signals.json            — latest monitor output
├── track_results.json      — latest track output
├── cultivate_results.json  — latest cultivate output
└── retro_weekly/
    └── YYYY-MM-DD-weekly.html — weekly reports
```
