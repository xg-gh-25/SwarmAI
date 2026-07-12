#!/usr/bin/env python3
"""Pollinate topic evaluation — scores a topic on 5 dimensions and recommends GO/DEFER/REJECT.

Usage:
    python evaluate_topic.py "Memory is the Moat" --domain "AI Architecture"
    python evaluate_topic.py "Python入门" --domain "General" --json

Outputs evaluation to stdout (human-readable or JSON).
"""
import argparse
import json
import sys
from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class TopicScore:
    knowledge_differentiation: int  # 0-5: Do we know something others don't?
    audience_match: int             # 0-5: Will AI practitioners care?
    asset_readiness: int            # 0-5: How much exists in Knowledge/?
    timeliness: int                 # 0-5: Evergreen(1), trending(3), breaking(5)
    production_complexity: int      # 0-5: Text-only(5) to custom 3D(1)


@dataclass
class Evaluation:
    topic: str
    domain: str
    scores: TopicScore
    roi: float
    recommendation: str  # GO / DEFER / REJECT
    format_recommendation: list
    rationale: str


WEIGHTS = {
    "knowledge_differentiation": 0.30,
    "audience_match": 0.25,
    "asset_readiness": 0.20,
    "timeliness": 0.15,
    "production_complexity": 0.10,
}

FORMAT_RULES = {
    "deep_technical":    ["video_horizontal", "article"],
    "industry_trend":    ["video_horizontal", "poster"],
    "quick_knowledge":   ["poster"],
    "breaking_news":     ["poster"],
    "framework":         ["video_horizontal", "article", "poster"],
    "default":           ["video_horizontal"],
}


def calculate_roi(scores: TopicScore) -> float:
    """Calculate weighted ROI score (0-5 scale)."""
    s = asdict(scores)
    return sum(s[dim] * WEIGHTS[dim] for dim in WEIGHTS)


def recommend(roi: float) -> str:
    """GO >= 3.0, DEFER 2.0-2.9, REJECT < 2.0."""
    if roi >= 3.0:
        return "GO"
    elif roi >= 2.0:
        return "DEFER"
    else:
        return "REJECT"


def recommend_formats(domain: str, scores: TopicScore) -> list:
    """Suggest output formats based on content type and scores."""
    if scores.timeliness >= 4:
        return FORMAT_RULES["breaking_news"]
    if scores.knowledge_differentiation >= 4 and scores.production_complexity >= 3:
        return FORMAT_RULES["deep_technical"]
    if scores.audience_match >= 4 and scores.asset_readiness >= 3:
        return FORMAT_RULES["framework"]
    if scores.timeliness >= 3:
        return FORMAT_RULES["industry_trend"]
    if scores.production_complexity >= 4:
        return FORMAT_RULES["quick_knowledge"]
    return FORMAT_RULES["default"]


def evaluate(topic: str, domain: str, scores: TopicScore,
             rationale: str = "") -> Evaluation:
    """Run full evaluation."""
    roi = calculate_roi(scores)
    rec = recommend(roi)
    formats = recommend_formats(domain, scores)
    return Evaluation(
        topic=topic,
        domain=domain,
        scores=scores,
        roi=round(roi, 2),
        recommendation=rec,
        format_recommendation=formats,
        rationale=rationale,
    )


def print_evaluation(ev: Evaluation, as_json: bool = False):
    """Print evaluation results."""
    if as_json:
        print(json.dumps(asdict(ev), indent=2, ensure_ascii=False))
        return

    s = ev.scores
    print(f"\n{'='*60}")
    print(f"POLLINATE EVALUATION: {ev.topic}")
    print(f"{'='*60}")
    print(f"Domain: {ev.domain}")
    print(f"\nScores:")
    print(f"  Knowledge Differentiation : {s.knowledge_differentiation}/5 (x0.30)")
    print(f"  Audience Match            : {s.audience_match}/5 (x0.25)")
    print(f"  Asset Readiness           : {s.asset_readiness}/5 (x0.20)")
    print(f"  Timeliness                : {s.timeliness}/5 (x0.15)")
    print(f"  Production Complexity     : {s.production_complexity}/5 (x0.10)")
    print(f"\n  ROI: {ev.roi:.2f}")
    print(f"  Recommendation: {ev.recommendation}")
    print(f"  Formats: {', '.join(ev.format_recommendation)}")
    if ev.rationale:
        print(f"\nRationale: {ev.rationale}")
    print(f"{'='*60}\n")


def main():
    parser = argparse.ArgumentParser(description="Evaluate a topic for Pollinate")
    parser.add_argument("topic", help="Topic to evaluate")
    parser.add_argument("--domain", default="General", help="Knowledge domain")
    parser.add_argument("--diff", type=int, default=3, help="Knowledge differentiation (0-5)")
    parser.add_argument("--audience", type=int, default=3, help="Audience match (0-5)")
    parser.add_argument("--readiness", type=int, default=3, help="Asset readiness (0-5)")
    parser.add_argument("--timeliness", type=int, default=2, help="Timeliness (0-5)")
    parser.add_argument("--complexity", type=int, default=3, help="Production complexity (0-5)")
    parser.add_argument("--rationale", default="", help="Evaluation rationale")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    scores = TopicScore(
        knowledge_differentiation=max(0, min(5, args.diff)),
        audience_match=max(0, min(5, args.audience)),
        asset_readiness=max(0, min(5, args.readiness)),
        timeliness=max(0, min(5, args.timeliness)),
        production_complexity=max(0, min(5, args.complexity)),
    )

    ev = evaluate(args.topic, args.domain, scores, args.rationale)
    print_evaluation(ev, as_json=args.json)

    # Exit code: 0 for GO, 1 for DEFER, 2 for REJECT
    sys.exit({"GO": 0, "DEFER": 1, "REJECT": 2}[ev.recommendation])


if __name__ == "__main__":
    main()
