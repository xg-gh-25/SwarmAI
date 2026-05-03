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
MAX_OUTPUT_TOKENS_MD = 3000   # markdown digest (longer, narrative)
MAX_OUTPUT_TOKENS_JSON = 1500  # JSON scores only (compact, structured)

# Trending feed IDs — signals from these feeds are tagged for hot-news routing
TRENDING_FEED_IDS = frozenset({"china-trending"})

# Per-tier quota for digest sampling — ensures diversity across feed types
_TIER_QUOTAS: dict[str, int] = {
    "frontier": 4,
    "leaders": 3,
    "research": 3,
    "engineering": 10,
    "aggregate": 10,
    "trending": 10,  # virtual tier for TRENDING_FEED_IDS
}


def _detect_lang(text: str) -> str:
    """Simple CJK detection — no external deps.

    Returns "zh" if >= 20% of characters are in CJK ranges, "en" otherwise.
    Covers CJK Unified Ideographs (U+4E00–9FFF), Extension A (U+3400–4DBF),
    Compatibility Ideographs (U+F900–FAFF), and fullwidth forms.
    """
    if not text:
        return "en"
    cjk_count = sum(
        1 for c in text
        if '㐀' <= c <= '鿿'    # CJK Unified + Extension A
        or '豈' <= c <= '﫿'    # Compatibility Ideographs
        or '　' <= c <= '〿'    # CJK Symbols and Punctuation
        or '＀' <= c <= '￯'    # Fullwidth Forms (，。！)
    )
    return "zh" if cjk_count > len(text) * 0.2 else "en"


def _sample_signals_for_digest(
    signals: list[RawSignal],
    max_total: int = 40,
) -> list[RawSignal]:
    """Sample signals with per-tier quotas to ensure diversity.

    Prevents the structural exclusion bug where feeds at config
    position 8+ (cn-ai, china-trending) are cut by a naive [:30] cap.

    Trending feeds get a virtual "trending" tier. Within each tier,
    newest signals come first. Unused quota redistributed to engineering.
    """
    from collections import defaultdict

    by_tier: dict[str, list[RawSignal]] = defaultdict(list)
    for s in signals:
        tier = "trending" if s.feed_id in TRENDING_FEED_IDS else s.tier
        by_tier[tier].append(s)

    # Sort each tier by published desc (newest first)
    for tier_signals in by_tier.values():
        tier_signals.sort(
            key=lambda s: (s.published or datetime.min.replace(tzinfo=timezone.utc)).timestamp(),
            reverse=True,
        )

    sampled: list[RawSignal] = []
    unused = 0
    for tier, quota in _TIER_QUOTAS.items():
        take = by_tier.get(tier, [])[:quota]
        sampled.extend(take)
        unused += quota - len(take)

    # Redistribute unused quota to engineering (highest actionability)
    if unused > 0:
        eng = by_tier.get("engineering", [])
        already = sum(1 for s in sampled if s.tier == "engineering"
                      and s.feed_id not in TRENDING_FEED_IDS)
        extra = eng[already:already + unused]
        sampled.extend(extra)

    return sampled[:max_total]


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

    # Per-tier quota sampling replaces naive [:30] cap (structural exclusion bug fix)
    sampled = _sample_signals_for_digest(signals, max_total=40)
    signal_text = "\n".join(
        f"- [idx={i}] [{tier_labels.get(s.tier, '⚙️ ENGINEERING')}] [{s.source}] {s.title} — {s.summary or 'No summary'} ({s.url})"
        for i, s in enumerate(sampled)
    )

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    _default_ctx = ("Building SwarmAI (AI desktop app), interested in AI agents, "
                     "Claude SDK, LLM frameworks, context engineering, "
                     "Chinese AI industry, Physical AI, 大模型应用.")
    ctx = user_context or _default_ctx

    _tier_guide = """- 🔵 FRONTIER LAB: Official AI lab announcements. Highest authority.
- 👤 LEADERS: AI thought leaders & founders. High-signal opinion.
- 🟣 RESEARCH: Academic papers and research blogs.
- ⚙️ ENGINEERING: Practitioner blogs and frameworks.
- 💭 OPINION: Thought leader commentary.
- 📰 AGGREGATE: Newsletters and aggregators."""

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # ── Call 1: Markdown digest ──────────────────────────────────────
    md_prompt = f"""You are Swarm's signal intelligence system. Produce a markdown digest.

## User Context
{ctx}

## Signal Tiers
{_tier_guide}

## Raw Signals
{signal_text}

## Output: Markdown Digest
Sections: 🔴 Act Now, 🟡 Worth Knowing, 🟢 Background.
For each signal: 1-2 sentence summary, "Why it matters", source tier tag, URL.
YAML frontmatter: date ({today}), signals_count, sources.
Skip irrelevant signals. Include Chinese signals if relevant to user context."""

    markdown_part, md_in, md_out = invoke(
        md_prompt, max_tokens=MAX_OUTPUT_TOKENS_MD, temperature=0.3,
    )

    # ── Call 2: JSON scores (separate call = never truncated) ────────
    json_prompt = f"""Score these signals for relevance. User context:
{ctx}

Signals:
{signal_text}

Output ONLY a JSON array — no markdown, no explanation:
[{{"idx": 0, "relevance_score": 0.85, "urgency": "high", "summary": "one-line"}}]

Rules:
- relevance_score: 0.0–1.0 based on relevance to user context
- urgency: "high" / "medium" / "low"
- Skip completely irrelevant signals (don't include idx)
- Include Chinese signals if they match user interests (中文AI, 具身智能, 大模型)
- Output valid JSON only"""

    json_content, js_in, js_out = invoke(
        json_prompt, max_tokens=MAX_OUTPUT_TOKENS_JSON, temperature=0.1,
    )
    total_tokens = md_in + md_out + js_in + js_out

    # Parse JSON scores
    scored_items: list[dict] = []
    try:
        json_text = json_content.strip()
        # Strip markdown code fences if LLM wraps output
        if json_text.startswith("```"):
            json_text = json_text.split("\n", 1)[-1]
        if json_text.endswith("```"):
            json_text = json_text.rsplit("```", 1)[0]
        raw_scores = json.loads(json_text.strip())
        for score_obj in raw_scores:
            idx = score_obj.get("idx", -1)
            if 0 <= idx < len(sampled):
                s = sampled[idx]
                raw_score = min(max(float(score_obj.get("relevance_score", 0.5)), 0), 1.0)
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
                    "lang": _detect_lang(s.title),
                    "feed_id": s.feed_id,
                    "platform": s.source if s.feed_id in TRENDING_FEED_IDS else "",
                    "rank": 0,
                    "region": "cn" if s.feed_id in TRENDING_FEED_IDS else "",
                })
    except (json.JSONDecodeError, ValueError, IndexError) as e:
        logger.warning(f"Failed to parse LLM JSON scores: {e}")
        scored_items = _keyword_scored_items(signals, ctx)

    if not scored_items:
        scored_items = _keyword_scored_items(signals, ctx)

    return markdown_part, scored_items, total_tokens


def _keyword_scored_items(signals: list[RawSignal], user_context: str = "") -> list[dict]:
    """Smart fallback: keyword-match scoring when LLM JSON parsing fails.

    Instead of flat 0.5 for everything, checks signal title + summary against
    keywords extracted from user_context. Matched signals get 0.7, unmatched
    get 0.3. This ensures "具身智能" scores higher than "茶颜悦色" even without LLM.
    """
    # Extract keywords from user context (simple word extraction)
    keywords: set[str] = set()
    if user_context:
        import re
        # English words (3+ chars)
        keywords.update(w.lower() for w in re.findall(r'[a-zA-Z]{3,}', user_context))
        # CJK phrases (2+ chars) — split on non-CJK
        cjk_chunks = re.findall(r'[一-鿿㐀-䶿]{2,}', user_context)
        keywords.update(cjk_chunks)
    # Always include core AI terms
    keywords.update({"agent", "llm", "model", "memory", "claude", "bedrock",
                     "智能", "模型", "agent", "AI"})

    now = datetime.now(timezone.utc).isoformat()
    items = []
    for s in signals:
        text = f"{s.title} {s.summary or ''}".lower()
        # Check keyword matches
        match_count = sum(1 for kw in keywords if kw.lower() in text)
        if match_count >= 2:
            raw_score = 0.8
            urgency = "medium"
        elif match_count == 1:
            raw_score = 0.6
            urgency = "low"
        else:
            raw_score = 0.3
            urgency = "low"

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
            "urgency": urgency,
            "fetched_at": now,
            "lang": _detect_lang(s.title),
            "feed_id": s.feed_id,
            "platform": s.source if s.feed_id in TRENDING_FEED_IDS else "",
            "rank": 0,
            "region": "cn" if s.feed_id in TRENDING_FEED_IDS else "",
        })
    return items


def _simple_scored_items(signals: list[RawSignal]) -> list[dict]:
    """Fallback: build scored items without LLM — flat 0.5 relevance, tier-weighted."""
    now = datetime.now(timezone.utc).isoformat()
    items = []
    for s in signals:
        # Clamp raw_score to [0, 1] — some adapters (github-trending) store
        # un-normalized values (star counts) in score. Without clamping, they
        # dominate the top-50 and crowd out all other feeds.
        raw_score = min(max(s.score, 0.5), 1.0)
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
            "lang": _detect_lang(s.title),
            "feed_id": s.feed_id,
            "platform": s.source if s.feed_id in TRENDING_FEED_IDS else "",
            "rank": 0,
            "region": "cn" if s.feed_id in TRENDING_FEED_IDS else "",
        })
    return items


def _write_l4_json(signals: list[RawSignal], scored_items: list[dict]) -> None:
    """Write Services/signals/signal_digest.json in the schema L4 expects.

    L4 consumer (proactive_intelligence.build_session_briefing_data) expects:
      { "items": [{ "fetched_at", "relevance_score", "title", "summary",
                     "source", "urgency", "feed_id", "lang", "platform",
                     "region" }] }

    The briefing builder routes items by feed_id: china-trending → hotNews
    section, everything else → signals section. Items missing feed_id are
    invisible to both sections.

    Merge strategy: new scored_items replace old items by title, stale items
    are evicted by age, and the result is capped at 50.
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

    # Schema guard: evict items missing required fields added in Briefing
    # Hub v2 (2026-04-26, commit 7bb365f). Without feed_id, the briefing
    # builder cannot route items to signals vs hotNews sections — they
    # become invisible. Stale pre-v2 items also dominate the relevance
    # sort and push fresh items out of the 50-item cap.
    existing_items = [
        it for it in existing_items
        if "feed_id" in it
    ]

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

    # Cap to 50 items with language diversity guarantee.
    # Without this, stale English items at 0.50 (fallback floor) crowd out
    # freshly-scored Chinese items that the LLM rated <0.50 for relevance.
    # Reserve up to 5 slots for non-English items so Chinese AI signals
    # always surface in the digest and Slack notifications.
    merged.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)
    zh_items = [it for it in merged if it.get("lang") == "zh"
                or any(ord(c) >= 0x4e00 for c in it.get("title", ""))]
    en_items = [it for it in merged if it not in zh_items]
    _ZH_RESERVE = 5
    zh_take = zh_items[:_ZH_RESERVE]
    en_take = en_items[:50 - len(zh_take)]
    merged = sorted(en_take + zh_take,
                    key=lambda x: x.get("relevance_score", 0), reverse=True)

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
        prompt, max_tokens=MAX_OUTPUT_TOKENS_MD, temperature=0.3,
    )

    header = (
        f"---\ndate: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n"
        f"type: weekly-rollup\nwindow_days: {window_days}\n---\n\n"
        f"# Weekly Signal Rollup\n\n"
    )

    return header + content, input_tokens + output_tokens
