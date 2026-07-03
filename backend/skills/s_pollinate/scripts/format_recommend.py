#!/usr/bin/env python3
"""
format_recommend.py — Pollinate v3 DISCOVER stage format recommendation engine.

Maps (audiences × outcomes × contexts) → recommended production tracks with priority.

Usage:
    python format_recommend.py --audiences leadership developer_community \
                               --outcomes alignment awareness \
                               --contexts meeting social_media \
                               --scope focused \
                               --json

Output: JSON with recommended tracks, priority order, and rationale for each.

No external dependencies. Pure mapping logic.
"""

import argparse
import json
import sys
from typing import Any

# ─── Canonical Values ─────────────────────────────────────────────────────────

AUDIENCES = {
    "leadership": {"label": "领导 (Rob/Bo)", "signals": ["领导", "rob", "bo", "leadership", "管理层", "boss"]},
    "customer": {"label": "客户/合作伙伴", "signals": ["客户", "customer", "partner", "合作伙伴", "client"]},
    "team": {"label": "团队内部", "signals": ["团队", "team", "内部", "internal", "同事"]},
    "developer_community": {"label": "开发者社区", "signals": ["社区", "community", "开发者", "developer", "github", "掘金"]},
    "social_followers": {"label": "社交媒体粉丝", "signals": ["粉丝", "followers", "社交", "social", "小红书", "朋友圈", "twitter"]},
}

OUTCOMES = {
    "awareness": {"label": "知道这件事存在", "signals": ["知道", "awareness", "了解", "传播", "spread"]},
    "alignment": {"label": "同意一个方向", "signals": ["同意", "alignment", "对齐", "approve", "方向", "决策"]},
    "action": {"label": "采取具体行动", "signals": ["行动", "action", "做", "try", "注册", "购买", "回复"]},
    "data_decision": {"label": "基于数据做决策", "signals": ["数据", "data", "决策", "分析", "metrics", "报告"]},
    "education": {"label": "深度理解一个概念", "signals": ["理解", "education", "学习", "learn", "深度", "教程"]},
}

CONTEXTS = {
    "meeting": {"label": "会议/演讲", "signals": ["会议", "meeting", "演讲", "present", "讲", "talk", "conference"]},
    "email": {"label": "邮件/异步阅读", "signals": ["邮件", "email", "异步", "async", "发给", "send"]},
    "social_media": {"label": "刷社交媒体", "signals": ["社交", "social", "小红书", "朋友圈", "twitter", "linkedin", "抖音"]},
    "search_learn": {"label": "主动搜索/学习", "signals": ["搜索", "search", "学习", "learn", "掘金", "blog", "youtube", "b站"]},
    "commute": {"label": "通勤/健身", "signals": ["通勤", "commute", "健身", "路上", "开车", "podcast", "播客"]},
    "analysis": {"label": "分析/对比", "signals": ["分析", "analysis", "对比", "compare", "评估", "benchmark"]},
}

# ─── Mapping Rules (Audience × Outcome × Context → Tracks) ───────────────────

# Each rule: (audience_set, outcome_set, context_set) → [(track, priority, rationale)]
# Sets use frozenset for matching — any match within the set triggers
MAPPING_RULES: list[dict[str, Any]] = [
    {
        "audiences": {"leadership"},
        "outcomes": {"alignment"},
        "contexts": {"meeting"},
        "tracks": [("deck", "P0", "structured argument for alignment in meeting")],
    },
    {
        "audiences": {"leadership"},
        "outcomes": {"data_decision"},
        "contexts": {"email", "analysis"},
        "tracks": [
            ("data_report", "P0", "data-driven decision support"),
            ("one_pager", "P1", "executive summary for quick scan"),
        ],
    },
    {
        "audiences": {"customer"},
        "outcomes": {"action"},
        "contexts": {"email"},
        "tracks": [("one_pager", "P0", "scannable CTA for customer action")],
    },
    {
        "audiences": {"customer"},
        "outcomes": {"alignment"},
        "contexts": {"meeting"},
        "tracks": [("deck", "P0", "customer pitch for alignment")],
    },
    {
        "audiences": {"developer_community", "social_followers"},
        "outcomes": {"awareness"},
        "contexts": {"social_media"},
        "tracks": [("poster", "P0", "visual hook for social feed awareness")],
    },
    {
        "audiences": {"developer_community"},
        "outcomes": {"education"},
        "contexts": {"search_learn"},
        "tracks": [
            ("narrative", "P0", "in-depth article for search/learn context"),
            ("video", "P1", "visual explanation for deeper understanding"),
        ],
    },
    {
        "audiences": {"team"},
        "outcomes": {"education"},
        "contexts": {"email", "search_learn"},
        "tracks": [("document", "P0", "guide/playbook for team education")],
    },
    {
        "audiences": {"social_followers"},
        "outcomes": {"awareness"},
        "contexts": {"social_media"},
        "tracks": [
            ("poster", "P0", "visual hook for feed scroll"),
            ("shorts", "P1", "vertical video for attention capture"),
        ],
    },
    # Cross-cutting: commute context REQUIRES context match (context is the strong signal)
    {
        "audiences": {"leadership", "customer", "team", "developer_community", "social_followers"},
        "outcomes": {"education", "awareness"},
        "contexts": {"commute"},
        "tracks": [("podcast", "P0", "audio format for commute/exercise context")],
        "require_context": True,  # Only fire if context specifically matches
    },
    # Cross-cutting: analysis context REQUIRES context match
    {
        "audiences": {"leadership", "customer", "team"},
        "outcomes": {"data_decision"},
        "contexts": {"analysis"},
        "tracks": [
            ("data_report", "P0", "structured data for decision-making"),
            ("interactive_report", "P1", "dashboard for exploratory analysis"),
        ],
        "require_context": True,
    },
]

# ─── Scope Modifiers ──────────────────────────────────────────────────────────

SCOPE_LIMITS = {
    "single": 1,       # 最核心的一个
    "focused": 3,      # 2-3 个覆盖主要受众
    "full": 99,        # 全套
    "recommend": 3,    # 不确定 — 你推荐 (default to focused)
}


def match_signals(user_input: str, category_map: dict) -> list[str]:
    """Extract canonical values from natural language input by signal matching."""
    user_lower = user_input.lower()
    matches = []
    for key, config in category_map.items():
        for signal in config["signals"]:
            if signal.lower() in user_lower:
                if key not in matches:
                    matches.append(key)
                break
    return matches


def recommend(
    audiences: list[str],
    outcomes: list[str],
    contexts: list[str],
    scope: str = "focused",
) -> dict[str, Any]:
    """
    Map discovery answers to recommended tracks.

    Returns:
        {
            "recommended_tracks": [{"track": str, "priority": str, "rationale": str}],
            "scope_applied": str,
            "reasoning": str
        }
    """
    # Collect all matching tracks with their priorities
    track_scores: dict[str, dict[str, Any]] = {}

    for rule in MAPPING_RULES:
        audience_match = bool(set(audiences) & rule["audiences"])
        outcome_match = bool(set(outcomes) & rule["outcomes"])
        context_match = bool(set(contexts) & rule["contexts"])

        # Rules with require_context=True need ALL 3 dimensions to match
        # (context is the distinguishing signal — without it, rule is too broad)
        require_context = rule.get("require_context", False)
        if require_context and not context_match:
            continue

        # Rule fires if at least 2 of 3 dimensions match (flexible matching)
        match_count = sum([audience_match, outcome_match, context_match])

        # Context-gated rules need all 3 to fire (prevent false positives)
        if require_context and match_count < 3:
            continue

        if match_count >= 2:
            for track, priority, rationale in rule["tracks"]:
                if track not in track_scores:
                    track_scores[track] = {
                        "track": track,
                        "priority": priority,
                        "rationale": rationale,
                        "match_strength": match_count,
                    }
                else:
                    # Upgrade priority if stronger match
                    existing = track_scores[track]
                    if match_count > existing["match_strength"]:
                        existing["priority"] = priority
                        existing["rationale"] = rationale
                        existing["match_strength"] = match_count

    # Sort by priority (P0 first) then match strength
    priority_order = {"P0": 0, "P1": 1, "P2": 2}
    sorted_tracks = sorted(
        track_scores.values(),
        key=lambda t: (priority_order.get(t["priority"], 9), -t["match_strength"]),
    )

    # Apply scope limit
    limit = SCOPE_LIMITS.get(scope, 3)
    final_tracks = sorted_tracks[:limit]
    deferred_tracks = sorted_tracks[limit:]

    # If no rules matched, fall back to poster (safest default)
    if not final_tracks:
        final_tracks = [{"track": "poster", "priority": "P0", "rationale": "default fallback — poster is universally applicable", "match_strength": 0}]
        fallback_triggered = True
    else:
        fallback_triggered = False

    # Split into supported (BUILD has instructions) vs unsupported (deferred until track built)
    supported_final = [t for t in final_tracks if t["track"] in SUPPORTED_TRACKS]
    unsupported_final = [t for t in final_tracks if t["track"] not in SUPPORTED_TRACKS]

    # Unsupported tracks from final get moved to deferred with explanation
    for t in unsupported_final:
        t["rationale"] = f"{t['rationale']} [DEFERRED: track not yet implemented in BUILD]"

    all_deferred = deferred_tracks + unsupported_final

    # If ALL confirmed tracks were unsupported, fall back to poster
    if not supported_final and not fallback_triggered:
        supported_final = [{"track": "poster", "priority": "P0", "rationale": "all recommended tracks are not yet implemented — defaulting to poster", "match_strength": 0}]
        fallback_triggered = True

    return {
        "recommended_tracks": [
            {"track": t["track"], "priority": t["priority"], "rationale": t["rationale"]}
            for t in supported_final
        ],
        "deferred_tracks": [
            {"track": t["track"], "priority": t["priority"], "rationale": t["rationale"]}
            for t in all_deferred
        ],
        "unsupported_requested": [t["track"] for t in unsupported_final],
        "fallback_triggered": fallback_triggered,
        "scope_applied": scope,
        "reasoning": (
            f"Matched {len(sorted_tracks)} tracks from {len(audiences)} audiences × "
            f"{len(outcomes)} outcomes × {len(contexts)} contexts. "
            f"Scope '{scope}' limits to {limit}."
            + (f" {len(unsupported_final)} tracks deferred (not yet in BUILD)." if unsupported_final else "")
        ),
    }


# Tracks that BUILD stage currently has instructions for.
# format_recommend.py will move unsupported tracks to deferred_tracks automatically.
SUPPORTED_TRACKS = {"poster", "video", "narrative", "shorts", "deck", "html_deck", "one_pager", "full_pdf", "data_report", "document", "ai_image", "interactive_report", "podcast"}


def _is_negated(user_lower: str, signal: str, match_pos: int) -> bool:
    """Check if a signal match is negated by IMMEDIATELY preceding words.

    Uses a narrow window (4 chars for Chinese, 10 for English) to avoid
    false positives where a negation earlier in the sentence targets a
    different word. E.g., "不要海报，只做文章" — "不要" targets "海报"
    not "文章" which is 5+ chars away.
    """
    # Chinese: narrow window (negation is typically 2-3 chars immediately before)
    cn_prefix = user_lower[max(0, match_pos - 4):match_pos]
    cn_negations = ["不要", "不做", "不用", "别做", "没有", "除了"]
    for neg in cn_negations:
        if cn_prefix.endswith(neg) or neg in cn_prefix:
            return True

    # English: wider window for multi-word negations ("don't want a", "do not need")
    en_prefix = user_lower[max(0, match_pos - 20):match_pos]
    en_negations = ["no ", "not ", "don't ", "without ", "skip ", "except ",
                    "don't want ", "don't need ", "do not ", "not a ", "no need for "]
    for neg in en_negations:
        if neg in en_prefix:
            return True

    return False


# NOTE: PPT→web-deck collision is handled directly in detect_fast_path's loop via
# the `"html_deck" in detected` guard (html_deck is iterated first). No separate
# window-scan helper — that ordered-detection test IS the collision fix, and it
# does not over-match a web/html TOPIC mention (which matches no html_deck phrase).


def detect_fast_path(user_message: str) -> list[str] | None:
    """
    Detect if user explicitly named formats in their message.
    Returns list of track names if fast-path detected, None otherwise.

    Handles negation: "不要海报" or "no poster" will NOT detect poster.
    Supports Chinese and English format names.
    """
    # NOTE: html_deck is listed BEFORE deck intentionally. Its signals ("html
    # deck", "网页ppt", "网页版", "转成网页", ...) are the self-contained browser
    # HTML-deck track, distinct from the PptxGenJS "deck" (PPTX) track. Because the
    # bare "deck"/"ppt" signal is a SUBSTRING of html-deck phrases, the loop below
    # suppresses the PPTX "deck" match when html_deck ALREADY fired
    # (`"html_deck" in detected`) — so an html-deck request does NOT double-emit,
    # while a PPTX that merely mentions web as a topic keeps its deck track.
    format_signals = {
        "html_deck": ["html deck", "html slides", "web deck", "html-deck",
                      "网页ppt", "网页幻灯", "网页演示", "html演示", "html幻灯",
                      "浏览器演示", "网页deck",
                      # PPT→web conversion intent (most natural phrasings) — these
                      # fire html_deck; the loop then suppresses the bare PPTX
                      # "deck"/"ppt" substring via the `"html_deck" in detected` guard.
                      "网页版", "转网页", "转成网页", "变成网页", "做成网页", "做个网页",
                      "转html", "转成html", "做成html",
                      "web version", "convert to web", "convert to html",
                      "to a web deck", "to web", "into a web", "web presentation",
                      "在线演示", "在线幻灯"],
        "poster": ["海报", "poster", "长图", "图片", "card"],
        "video": ["视频", "video", "b站", "bilibili", "youtube"],
        "narrative": ["文章", "narrative", "article", "长文", "掘金", "公众号", "blog"],
        "shorts": ["短视频", "shorts", "reels", "抖音", "竖屏"],
        "deck": ["deck", "ppt", "pptx", "slides", "演示", "幻灯片"],
        "one_pager": ["one-pager", "one pager", "单页", "pdf", "一页纸"],
        "data_report": ["数据报告", "data report", "excel", "xlsx", "报表"],
        "document": ["文档", "document", "docx", "六页纸", "six-pager", "白皮书", "white paper"],
        "podcast": ["播客", "podcast", "音频", "audio"],
        "ai_image": ["hero image", "ai image", "generate image", "illustration", "生成图", "配图", "ai图"],
        "interactive_report": ["dashboard", "interactive", "仪表盘", "scorecard", "交互报告"],
    }

    user_lower = user_message.lower()
    detected = []

    for track, signals in format_signals.items():
        for signal in signals:
            pos = user_lower.find(signal)
            if pos != -1:
                # Check for negation before the match
                if _is_negated(user_lower, signal, pos):
                    break
                # Collision guard: suppress the PPTX "deck" track ONLY when the
                # html_deck track ALREADY fired (html_deck is iterated FIRST). This
                # is the precise "the user asked for an html deck" signal — "html
                # deck"/"网页ppt"/"把ppt转成网页版" all match an html_deck phrase →
                # html_deck in `detected` → suppress the substring "deck"/"ppt"
                # double-emit. A message that merely MENTIONS 网页/web as a TOPIC
                # ("做个关于网页设计的ppt") matches NO html_deck phrase → html_deck
                # NOT in detected → "deck" correctly stays (it's a PPTX about web
                # design). Replaces an earlier window-scan that over-matched topic
                # mentions and silently dropped the PPTX request (Gate-2 HIGH).
                if track == "deck" and "html_deck" in detected:
                    break
                if track not in detected:
                    detected.append(track)
                break

    return detected if detected else None


def main():
    parser = argparse.ArgumentParser(description="Pollinate format recommendation engine")
    parser.add_argument("--audiences", nargs="+", default=[], help="Canonical audience values")
    parser.add_argument("--outcomes", nargs="+", default=[], help="Canonical outcome values")
    parser.add_argument("--contexts", nargs="+", default=[], help="Canonical context values")
    parser.add_argument("--scope", default="focused", choices=list(SCOPE_LIMITS.keys()))
    parser.add_argument("--message", default="", help="Raw user message for fast-path detection")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    # Fast-path detection
    fast_path = None
    if args.message:
        fast_path = detect_fast_path(args.message)

    if fast_path:
        # Filter fast-path results against SUPPORTED_TRACKS
        supported = [t for t in fast_path if t in SUPPORTED_TRACKS]
        unsupported = [t for t in fast_path if t not in SUPPORTED_TRACKS]

        result = {
            "mode": "fast_path",
            "detected_formats": supported if supported else ["poster"],  # fallback if all unsupported
            "reasoning": f"User explicitly mentioned: {', '.join(fast_path)}. Skip discovery questions.",
        }
        if unsupported:
            result["unsupported_detected"] = unsupported
            result["reasoning"] += f" Note: {', '.join(unsupported)} not yet supported — excluded."
        if not supported:
            result["fallback_triggered"] = True
            result["reasoning"] += " All detected formats unsupported — defaulting to poster."
    else:
        result = recommend(
            audiences=args.audiences,
            outcomes=args.outcomes,
            contexts=args.contexts,
            scope=args.scope,
        )
        result["mode"] = "discovery"

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        if result["mode"] == "fast_path":
            print(f"Fast-path detected: {', '.join(result['detected_formats'])}")
        else:
            print(f"Recommended tracks ({result['scope_applied']}):")
            for t in result["recommended_tracks"]:
                print(f"  {t['priority']}: {t['track']} — {t['rationale']}")
            if result.get("deferred_tracks"):
                print(f"\nDeferred:")
                for t in result["deferred_tracks"]:
                    print(f"  {t['priority']}: {t['track']} — {t['rationale']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
