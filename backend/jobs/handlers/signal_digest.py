"""
Signal Digest Handler

Takes buffered raw signals, groups them, calls Bedrock Sonnet 4.6 for
LLM summarization with relevance scoring, and writes:
  1. A human-readable markdown digest → Knowledge/Signals/
  2. A machine-readable JSON file → Services/signals/signal_digest.json
     (consumed by L4 proactive_intelligence._get_signal_highlights)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..models import JobResult, RawSignal, SchedulerState, TIER_WEIGHTS

logger = logging.getLogger(__name__)

# Output directories — relative to SwarmWS root
SWARMWS = Path(os.environ.get(
    "SWARMWS_DIR",
    os.path.expanduser("~/.swarm-ai/SwarmWS"),
))
SIGNALS_DIR = Path(os.environ.get(
    "SWARM_SIGNALS_DIR",
    str(SWARMWS / "Knowledge" / "Signals"),
))
# L4 consumer reads this JSON file
L4_DIGEST_PATH = SWARMWS / "Services" / "signals" / "signal_digest.json"

# Bedrock config
MAX_INPUT_TOKENS = 4000
MAX_OUTPUT_TOKENS = 2000


def handle_signal_digest(
    state: SchedulerState,
    user_context: str = "",
    window_days: int | None = None,
) -> JobResult:
    """
    Digest signals into a markdown summary file.

    In normal mode (window_days=None): digests buffered raw_signals from the
    most recent fetch and clears the buffer.

    In rollup mode (window_days=N): re-reads the last N days of markdown
    digests from Knowledge/Signals/ and produces a weekly summary. Does NOT
    clear the raw_signals buffer (rollup is a read-only aggregation).

    Args:
        state: Scheduler state with raw_signals buffer
        user_context: Summary of user interests/projects for relevance scoring
        window_days: If set, produce a rollup digest from the last N days

    Returns:
        JobResult with output_path to the digest file
    """
    start = datetime.now(timezone.utc)

    # Weekly rollup mode: aggregate existing digests
    if window_days:
        return _handle_rollup(state, user_context, window_days, start)

    if not state.raw_signals:
        logger.info("No buffered signals to digest")
        return JobResult(
            job_id="signal-digest",
            timestamp=start,
            status="skipped",
            summary="No signals to digest",
            duration_seconds=0,
        )

    signals = state.raw_signals[:]
    logger.info(f"Digesting {len(signals)} buffered signals")

    # Try LLM digest, fall back to simple formatting
    scored_items: list[dict] = []
    try:
        digest_md, scored_items, tokens_used = _llm_digest(signals, user_context)
    except Exception as e:
        logger.warning(f"LLM digest failed, using simple format: {e}")
        digest_md = _simple_digest(signals)
        scored_items = _simple_scored_items(signals)
        tokens_used = 0

    # Write digest file
    SIGNALS_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    digest_path = SIGNALS_DIR / f"{today}-digest.md"

    # Append if file exists (multiple digests per day)
    if digest_path.exists():
        existing = digest_path.read_text()
        digest_md = existing + "\n\n---\n\n" + digest_md
    digest_path.write_text(digest_md)

    # Write L4 JSON digest for proactive_intelligence._get_signal_highlights()
    _write_l4_json(signals, scored_items)

    # Clear processed signals from buffer
    state.raw_signals.clear()
    state.monthly_tokens_used += tokens_used

    duration = (datetime.now(timezone.utc) - start).total_seconds()
    summary = f"Digested {len(signals)} signals → {digest_path.name} + signal_digest.json ({tokens_used} tokens)"
    logger.info(summary)

    return JobResult(
        job_id="signal-digest",
        timestamp=datetime.now(timezone.utc),
        status="success",
        summary=summary,
        output_path=str(digest_path),
        tokens_used=tokens_used,
        signals_count=len(signals),
        duration_seconds=duration,
    )


def _llm_digest(
    signals: list[RawSignal], user_context: str
) -> tuple[str, list[dict], int]:
    """
    Use Bedrock Sonnet 4.6 to create a prioritized, annotated digest.

    Returns:
        (markdown_content, scored_items, tokens_used)
    """
    from jobs.bedrock import invoke

    # Tier descriptions for prompt context
    tier_labels = {
        "frontier": "🔵 FRONTIER LAB (highest authority — official AI lab blog)",
        "leaders": "👤 LEADERS (AI thought leaders & founders — high-signal opinion)",
        "research": "🟣 RESEARCH (academic/research — trend indicator)",
        "engineering": "⚙️ ENGINEERING (practitioner blog/framework)",
        "opinion": "💭 OPINION (thought leader commentary)",
        "aggregate": "📰 AGGREGATE (newsletter/aggregator — second-hand signal)",
    }

    # Build signal summaries for the prompt (now with tier)
    signal_text = "\n".join(
        f"- [idx={i}] [{tier_labels.get(s.tier, '⚙️ ENGINEERING')}] [{s.source}] {s.title} — {s.summary or 'No summary'} ({s.url})"
        for i, s in enumerate(signals[:30])  # cap to control token usage
    )

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    prompt = f"""You are Swarm's signal intelligence system. Analyze these signals and produce TWO outputs.

## User Context
{user_context or "Building SwarmAI (AI desktop app), interested in AI agents, Claude SDK, LLM frameworks, context engineering."}

## Signal Tier System
Each signal is tagged with a source authority tier. Weight your scoring accordingly:
- 🔵 FRONTIER LAB: Official AI lab announcements. Highest authority — these define the landscape.
- 👤 LEADERS: AI thought leaders & founders (Sam Altman, Karpathy, etc.). High-signal opinion — score like frontier.
- 🟣 RESEARCH: Academic papers and research blogs. Trend indicators for 3-12 month horizon.
- ⚙️ ENGINEERING: Practitioner blogs and frameworks. Direct actionability.
- 💭 OPINION: Thought leader commentary. Directional, not definitive.
- 📰 AGGREGATE: Newsletters and aggregators. Useful for coverage but second-hand.

Frontier, Leaders, and Research signals should be scored higher for relevance when equally interesting.

## Raw Signals
{signal_text}

## Output 1: Markdown Digest
Create a markdown digest with these sections:
1. **🔴 Act Now** — signals requiring immediate attention or action
2. **🟡 Worth Knowing** — interesting developments relevant to our work
3. **🟢 Background** — general industry movement, nice to know

For each signal: 1-2 sentence summary, "Why it matters" annotation, source tier tag, original URL.
Start with YAML frontmatter: date, signals_count, sources (unique source names).
Skip irrelevant signals entirely.

## Output 2: JSON Scores
After the markdown, output a line "---JSON---" then a JSON array of objects, one per RELEVANT signal (skip irrelevant ones):
```
[{{"idx": 0, "relevance_score": 0.85, "urgency": "high", "summary": "one-line summary"}}]
```
- relevance_score: 0.0 to 1.0 based on relevance to user context (before tier weighting — we apply tier multipliers post-hoc)
- urgency: "high" (act now), "medium" (worth knowing), "low" (background)
- summary: concise one-line summary

Output the markdown first, then ---JSON--- separator, then the JSON array. Nothing else."""

    content, input_tokens, output_tokens = invoke(
        prompt, max_tokens=MAX_OUTPUT_TOKENS, temperature=0.3,
    )
    total_tokens = input_tokens + output_tokens

    # Split markdown and JSON parts
    scored_items: list[dict] = []
    markdown_part = content
    if "---JSON---" in content:
        parts = content.split("---JSON---", 1)
        markdown_part = parts[0].strip()
        try:
            json_text = parts[1].strip()
            # Strip markdown code fences if present
            if json_text.startswith("```"):
                json_text = json_text.split("\n", 1)[-1]
            if json_text.endswith("```"):
                json_text = json_text.rsplit("```", 1)[0]
            raw_scores = json.loads(json_text.strip())
            # Map idx back to signal data, apply tier weighting
            for score_obj in raw_scores:
                idx = score_obj.get("idx", -1)
                if 0 <= idx < len(signals):
                    s = signals[idx]
                    raw_score = float(score_obj.get("relevance_score", 0.5))
                    tier_weight = TIER_WEIGHTS.get(s.tier, 1.0)
                    weighted_score = min(raw_score * tier_weight, 1.0)
                    scored_items.append({
                        "title": s.title,
                        "summary": score_obj.get("summary", s.summary or ""),
                        "source": s.source,
                        "url": s.url,
                        "relevance_score": round(weighted_score, 3),
                        "raw_relevance_score": round(raw_score, 3),
                        "tier": s.tier,
                        "tier_weight": tier_weight,
                        "urgency": score_obj.get("urgency", "low"),
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                    })
        except (json.JSONDecodeError, ValueError, IndexError) as e:
            logger.warning(f"Failed to parse LLM JSON scores: {e}")
            scored_items = _simple_scored_items(signals)

    if not scored_items:
        scored_items = _simple_scored_items(signals)

    return markdown_part, scored_items, total_tokens


def _simple_scored_items(signals: list[RawSignal]) -> list[dict]:
    """Fallback: build scored items without LLM — flat 0.5 relevance, tier-weighted."""
    now = datetime.now(timezone.utc).isoformat()
    items = []
    for s in signals:
        raw_score = max(s.score, 0.5)
        tier_weight = TIER_WEIGHTS.get(s.tier, 1.0)
        weighted_score = min(raw_score * tier_weight, 1.0)
        items.append({
            "title": s.title,
            "summary": (s.summary or "")[:200],
            "source": s.source,
            "url": s.url,
            "relevance_score": round(weighted_score, 3),
            "raw_relevance_score": round(raw_score, 3),
            "tier": s.tier,
            "tier_weight": tier_weight,
            "urgency": "medium" if weighted_score >= 0.7 else "low",
            "fetched_at": now,
        })
    return items


def _write_l4_json(signals: list[RawSignal], scored_items: list[dict]) -> None:
    """Write Services/signals/signal_digest.json in the schema L4 expects.

    L4 consumer (proactive_intelligence._get_signal_highlights) expects:
      { "items": [{ "fetched_at", "relevance_score", "title", "summary", "source", "urgency" }] }
    """
    L4_DIGEST_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Merge with existing items: evict stale (>48h), dedup by title, cap 50
    existing_items: list[dict] = []
    if L4_DIGEST_PATH.exists():
        try:
            existing = json.loads(L4_DIGEST_PATH.read_text(encoding="utf-8"))
            existing_items = existing.get("items", [])
        except (json.JSONDecodeError, OSError):
            pass

    # Two-tier eviction:
    # 1. Soft eviction (48h) — only when new items replace them. Prevents
    #    empty L4 JSON when the fetcher has a temporary failure.
    # 2. Hard eviction (7 days) — always runs. Prevents indefinitely stale
    #    data if the signal fetcher is broken for days.
    hard_cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    existing_items = [
        it for it in existing_items
        if it.get("fetched_at", "") >= hard_cutoff
    ]

    if scored_items:
        soft_cutoff = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        existing_items = [
            it for it in existing_items
            if it.get("fetched_at", "") >= soft_cutoff
        ]

    # Dedup: keep existing items whose titles don't overlap with new ones
    new_titles = {item["title"] for item in scored_items}
    merged = [it for it in existing_items if it.get("title") not in new_titles]
    merged.extend(scored_items)

    # Cap to 50 items, sorted by relevance_score desc
    merged.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)
    merged = merged[:50]

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "signals_count": len(merged),
        "items": merged,
    }

    L4_DIGEST_PATH.write_text(
        json.dumps(payload, indent=2, default=str),
        encoding="utf-8",
    )
    logger.info(f"L4 JSON digest written: {L4_DIGEST_PATH} ({len(merged)} items)")


def _simple_digest(signals: list[RawSignal]) -> str:
    """Fallback: format signals as simple markdown without LLM."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    sources = list({s.source for s in signals})

    lines = [
        "---",
        f'date: "{today}"',
        f"signals_count: {len(signals)}",
        f"sources: {sources}",
        "format: simple",
        "---",
        "",
        f"# Signal Digest — {today}",
        "",
        "*LLM digest unavailable — raw signals listed below.*",
        "",
    ]

    for s in signals:
        published = s.published.strftime("%H:%M UTC") if s.published else "unknown"
        lines.append(f"### {s.title}")
        lines.append(f"**Source:** {s.source} | **Published:** {published}")
        if s.summary:
            lines.append(f"\n{s.summary}")
        lines.append(f"\n→ [{s.url}]({s.url})")
        lines.append("")

    return "\n".join(lines)


# ── Weekly Rollup ────────────────────────────────────────────────────


def _handle_rollup(
    state: SchedulerState,
    user_context: str,
    window_days: int,
    start: datetime,
) -> JobResult:
    """Produce a weekly rollup by re-reading daily digest markdown files.

    Collects all digest files from the last `window_days` days, extracts
    signal entries, and runs them through the LLM to produce a consolidated
    weekly summary highlighting the most important trends.
    """
    now = datetime.now(timezone.utc)
    collected_content = []

    for days_ago in range(window_days):
        date_str = (now - timedelta(days=days_ago)).strftime("%Y-%m-%d")
        digest_path = SIGNALS_DIR / f"{date_str}-digest.md"
        if digest_path.exists():
            content = digest_path.read_text(encoding="utf-8")
            collected_content.append(f"## {date_str}\n{content}")

    if not collected_content:
        return JobResult(
            job_id="weekly-rollup",
            timestamp=start,
            status="skipped",
            summary=f"No digest files found in the last {window_days} days",
            duration_seconds=0,
        )

    combined = "\n\n---\n\n".join(collected_content)
    logger.info(f"Weekly rollup: aggregating {len(collected_content)} daily digests")

    # Try LLM rollup, fall back to concatenation
    tokens_used = 0
    try:
        rollup_md, tokens_used = _llm_rollup(combined, user_context, window_days)
    except Exception as e:
        logger.warning(f"LLM rollup failed, using concatenation: {e}")
        rollup_md = (
            f"# Weekly Signal Rollup — {window_days} days\n\n"
            f"*LLM rollup unavailable. Raw digests concatenated below.*\n\n"
            f"{combined}"
        )

    # Write rollup file
    SIGNALS_DIR.mkdir(parents=True, exist_ok=True)
    today = now.strftime("%Y-%m-%d")
    rollup_path = SIGNALS_DIR / f"{today}-weekly.md"
    rollup_path.write_text(rollup_md)

    state.monthly_tokens_used += tokens_used
    duration = (datetime.now(timezone.utc) - start).total_seconds()

    return JobResult(
        job_id="weekly-rollup",
        timestamp=datetime.now(timezone.utc),
        status="success",
        summary=f"Weekly rollup: {len(collected_content)} days → {rollup_path.name} ({tokens_used} tokens)",
        output_path=str(rollup_path),
        tokens_used=tokens_used,
        signals_count=len(collected_content),
        duration_seconds=duration,
    )


def _llm_rollup(combined_digests: str, user_context: str, window_days: int) -> tuple[str, int]:
    """Use Bedrock Sonnet 4.6 to produce a weekly rollup summary."""
    from jobs.bedrock import invoke

    # Truncate to fit context
    truncated = combined_digests[:8000]

    prompt = f"""You are Swarm's signal intelligence system. Create a WEEKLY ROLLUP from {window_days} days of daily signal digests.

## User Context
{user_context or "Building SwarmAI (AI desktop app), interested in AI agents, Claude SDK, LLM frameworks, context engineering."}

## Daily Digests (last {window_days} days)
{truncated}

## Output: Weekly Rollup Markdown
Create a concise weekly summary with:
1. **Key Trends** — 3-5 themes that emerged this week
2. **Notable Releases** — important tool/framework releases
3. **Action Items** — things that should influence our work
4. **What to Watch** — emerging topics worth monitoring

Be concise. This is a rollup, not a repeat — synthesize patterns, don't list individual signals."""

    content, input_tokens, output_tokens = invoke(
        prompt, max_tokens=MAX_OUTPUT_TOKENS, temperature=0.3,
    )

    header = (
        f"---\ndate: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n"
        f"type: weekly-rollup\nwindow_days: {window_days}\n---\n\n"
        f"# Weekly Signal Rollup\n\n"
    )

    return header + content, input_tokens + output_tokens
