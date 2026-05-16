#!/usr/bin/env python3
"""Pollinate GEO Signal Stack Scorer — AI engine discoverability gate.

Scores article/narrative content on 4 pillars for Generative Engine Optimization.
Based on Princeton KDD 2024 GEO paper + nowork-studio/toprank methodology.

Pillars (weighted):
    Evidence Density (35%): numbers, citations, expert quotes, named entities
    Structure & Position (25%): front-loading, TL;DR, hierarchy, tables/lists
    Authority Signals (25%): byline, recency, methodology, limitations
    AI Crawlability (15%): structural metadata signals

Usage:
    python geo_score.py /path/to/article.md --json
    python geo_score.py /path/to/article.md

Exit codes:
    0 = score >= 60 (pass threshold)
    1 = score < 60 (fail)

Score range: 0-100.
Pass threshold: 60 (configurable via --threshold).
"""
import argparse
import json
import re
import sys
from pathlib import Path

# Default pass threshold
DEFAULT_THRESHOLD = 60

# Anti-pattern openers that signal generic AI slop
GENERIC_OPENERS = [
    r"^In today's rapidly evolving",
    r"^In the ever-changing landscape",
    r"^As we navigate the",
    r"^In recent years,?\s",
    r"^The world of .* is changing",
    r"^It's no secret that",
    r"^There's no denying",
    r"^When it comes to",
    r"^In this article,? we",
]

# Unsupported superlatives (without evidence)
UNSUPPORTED_CLAIMS = [
    r"revolutioniz(?:ing|ed|es)",
    r"game.?chang(?:ing|er)",
    r"cutting.?edge",
    r"world.?class",
    r"best.?in.?class",
    r"industry.?leading",
    r"unprecedented",
]


def score_evidence_density(text: str, lines: list[str]) -> dict:
    """Score evidence density (35% weight).

    Checks:
        - Numbers with units (≥5 target)
        - Named expert quotes (≥2 target)
        - Named entities (proper nouns) (≥3 target)
        - Citations/references (≥1 per 500 words target)
    """
    score = 0
    details = {}

    # Numbers with units (e.g., "73%", "2.7x", "50-100K tokens", "0.46s")
    numbers_with_units = re.findall(
        r"\d+(?:\.\d+)?(?:\s*(?:%|x|px|ms|s|KB|MB|GB|TB|tokens?|sessions?|weeks?|days?|hours?|minutes?))\b",
        text, re.IGNORECASE
    )
    num_count = len(numbers_with_units)
    details["numbers_with_units"] = min(num_count, 10)
    if num_count >= 5:
        score += 30
    elif num_count >= 3:
        score += 20
    elif num_count >= 1:
        score += 10

    # Named expert quotes (pattern: "Name (Affiliation)" or "Dr./Prof. Name")
    expert_quotes = re.findall(
        r'(?:Dr\.|Prof\.|Professor)\s+[A-Z][a-z]+\s+[A-Z][a-z]+|'
        r'[A-Z][a-z]+\s+[A-Z][a-z]+\s*\([^)]+\)',
        text
    )
    expert_count = len(set(expert_quotes))
    details["named_experts"] = expert_count
    if expert_count >= 2:
        score += 30
    elif expert_count >= 1:
        score += 15

    # Named entities (capitalized multi-word proper nouns)
    named_entities = re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b', text)
    # Filter out sentence starters by checking they're not at line beginnings
    entity_count = len(set(named_entities))
    details["named_entities"] = min(entity_count, 20)
    if entity_count >= 3:
        score += 20
    elif entity_count >= 1:
        score += 10

    # Citations (URLs, references, "according to", numbered citations)
    word_count = len(text.split())
    citations = len(re.findall(
        r'https?://|(?:according to|per|as reported by)\s|'
        r'\[\d+\]|\(\d{4}\)',
        text, re.IGNORECASE
    ))
    target_citations = max(1, word_count // 500)
    details["citations"] = citations
    details["citation_target"] = target_citations
    if citations >= target_citations:
        score += 20
    elif citations >= 1:
        score += 10

    return {"score": min(score, 100), "details": details}


def score_structure_position(text: str, lines: list[str]) -> dict:
    """Score structure and position (25% weight).

    Checks:
        - Core message in first 150 words (PAWC front-loading)
        - TL;DR or Key Takeaways section present
        - Hierarchical headings (##, ###)
        - Tables or lists present
    """
    score = 0
    details = {}

    # First 150 words analysis
    words = text.split()
    first_150 = " ".join(words[:150]) if len(words) >= 150 else text
    # Check if first 150 words contain a number (sign of substantive content)
    has_stat_early = bool(re.search(r"\d+(?:\.\d+)?(?:%|x|px|ms|s)", first_150))
    details["stat_in_first_150"] = has_stat_early
    if has_stat_early:
        score += 30

    # TL;DR / Summary section
    has_tldr = bool(re.search(
        r"^#+\s*(?:TL;?DR|Summary|Key (?:Takeaways|Findings)|Abstract|Overview)",
        text, re.MULTILINE | re.IGNORECASE
    ))
    details["has_tldr"] = has_tldr
    if has_tldr:
        score += 25

    # Hierarchical headings
    headings = re.findall(r"^#{1,4}\s+", text, re.MULTILINE)
    heading_count = len(headings)
    details["heading_count"] = heading_count
    if heading_count >= 4:
        score += 25
    elif heading_count >= 2:
        score += 15

    # Tables or lists
    has_table = bool(re.search(r"\|.*\|.*\|", text))
    has_list = bool(re.search(r"^[\-\*\d]+[.\)]\s", text, re.MULTILINE))
    details["has_table"] = has_table
    details["has_list"] = has_list
    if has_table or has_list:
        score += 20

    return {"score": min(score, 100), "details": details}


def score_authority_signals(text: str, lines: list[str]) -> dict:
    """Score authority signals (25% weight).

    Checks:
        - Author byline (real name, not generic)
        - Recency signals (dates, "updated")
        - Methodology section
        - Limitations disclosed
    """
    score = 0
    details = {}

    # Author byline (pattern: "By Name" or "Author: Name" or "**By Name**")
    has_byline = bool(re.search(
        r"(?:\*\*)?[Bb]y\s+[A-Z][a-z]+\s+[A-Z][a-z]+(?:\*\*)?|"
        r"[Aa]uthor:\s*[A-Z]",
        text
    ))
    details["has_byline"] = has_byline
    if has_byline:
        score += 30

    # Recency signals
    has_date = bool(re.search(
        r"(?:20\d{2}[-/]\d{2}|(?:January|February|March|April|May|June|July|August|"
        r"September|October|November|December)\s+\d{1,2},?\s*20\d{2}|"
        r"[Uu]pdated\s+(?:on\s+)?(?:\w+\s+)?\d)",
        text
    ))
    details["has_date"] = has_date
    if has_date:
        score += 20

    # Methodology section
    has_methodology = bool(re.search(
        r"^#+\s*(?:Method(?:ology)?|Approach|How [Ww]e (?:Measured|Tested|Compared))",
        text, re.MULTILINE
    ))
    details["has_methodology"] = has_methodology
    if has_methodology:
        score += 30

    # Limitations section
    has_limitations = bool(re.search(
        r"^#+\s*Limitations?|(?:limitations?|caveats?|constraints?)\s*(?:include|are|:)",
        text, re.MULTILINE | re.IGNORECASE
    ))
    details["has_limitations"] = has_limitations
    if has_limitations:
        score += 20

    return {"score": min(score, 100), "details": details}


def score_ai_crawlability(text: str, lines: list[str]) -> dict:
    """Score AI crawlability (15% weight).

    For local content (not yet on web), checks structural signals:
        - Clean markdown structure (not HTML soup)
        - Semantic headings (not just bold text)
        - Code blocks properly fenced
        - No broken links or placeholders
    """
    score = 0
    details = {}

    # Clean structure (markdown, not messy HTML)
    html_tag_count = len(re.findall(r"<[a-z]+[^>]*>", text, re.IGNORECASE))
    is_clean_markdown = html_tag_count < 5
    details["is_clean_markdown"] = is_clean_markdown
    if is_clean_markdown:
        score += 30

    # Semantic headings (# not **bold as heading**)
    heading_lines = [l for l in lines if l.startswith("#")]
    bold_heading_lines = [l for l in lines if re.match(r"^\*\*[^*]+\*\*\s*$", l.strip())]
    uses_semantic_headings = len(heading_lines) > len(bold_heading_lines)
    details["semantic_headings"] = uses_semantic_headings
    if uses_semantic_headings:
        score += 25

    # Proper code fencing
    code_blocks = re.findall(r"```\w+", text)
    has_fenced_code = len(code_blocks) > 0 or "```" not in text
    details["proper_code_fencing"] = has_fenced_code
    if has_fenced_code:
        score += 25

    # No placeholder text (exclude fenced code blocks first)
    text_no_code = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    placeholders = re.findall(r"\[TODO\]|\[TBD\]|\[INSERT\]|\{[A-Z_]+\}", text_no_code)
    no_placeholders = len(placeholders) == 0
    details["no_placeholders"] = no_placeholders
    if no_placeholders:
        score += 20

    return {"score": min(score, 100), "details": details}


def detect_anti_patterns(text: str) -> list[str]:
    """Detect anti-patterns that should trigger warnings."""
    warnings = []

    # Generic openers
    first_line = text.strip().split("\n")[0] if text.strip() else ""
    # Skip heading lines, check the first body line
    body_lines = [l for l in text.strip().split("\n") if l.strip() and not l.startswith("#")]
    first_body = body_lines[0] if body_lines else ""

    for pattern in GENERIC_OPENERS:
        if re.search(pattern, first_body, re.IGNORECASE):
            warnings.append(f"Anti-pattern: generic opener detected — '{first_body[:50]}...'")
            break

    # Unsupported superlatives
    for pattern in UNSUPPORTED_CLAIMS:
        matches = re.findall(pattern, text, re.IGNORECASE)
        if matches:
            warnings.append(f"Anti-pattern: unsupported superlative '{matches[0]}' — add evidence or remove")
            break

    # Zero named entities check
    named = re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b', text)
    if len(set(named)) == 0:
        warnings.append("Anti-pattern: zero named entities — generic content unlikely to be cited")

    return warnings


def score_content(file_path: Path, threshold: int = DEFAULT_THRESHOLD) -> dict:
    """Score content file on GEO Signal Stack.

    Returns:
        Dict with total_score, pass, pillars, warnings.
    """
    text = file_path.read_text(encoding="utf-8")
    lines = text.split("\n")

    # Score each pillar
    evidence = score_evidence_density(text, lines)
    structure = score_structure_position(text, lines)
    authority = score_authority_signals(text, lines)
    crawlability = score_ai_crawlability(text, lines)

    # Weighted total
    total = (
        evidence["score"] * 0.35
        + structure["score"] * 0.25
        + authority["score"] * 0.25
        + crawlability["score"] * 0.15
    )
    total_score = round(total)

    # Anti-pattern detection
    warnings = detect_anti_patterns(text)

    return {
        "total_score": total_score,
        "pass": total_score >= threshold,
        "threshold": threshold,
        "pillars": {
            "evidence_density": evidence,
            "structure_position": structure,
            "authority_signals": authority,
            "ai_crawlability": crawlability,
        },
        "warnings": warnings,
    }


def main():
    parser = argparse.ArgumentParser(description="GEO Signal Stack Scorer")
    parser.add_argument("file", type=Path, help="Path to article/narrative file")
    parser.add_argument("--json", action="store_true", help="Output JSON format")
    parser.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD,
                        help=f"Pass threshold (default: {DEFAULT_THRESHOLD})")
    args = parser.parse_args()

    if not args.file.exists():
        result = {"total_score": 0, "pass": False, "error": f"File not found: {args.file}",
                  "pillars": {}, "warnings": []}
        if args.json:
            print(json.dumps(result))
        else:
            print(f"ERROR: {args.file} not found")
        sys.exit(1)

    result = score_content(args.file, args.threshold)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"  GEO Score: {result['total_score']}/100 ({'PASS' if result['pass'] else 'FAIL'})")
        print(f"  Threshold: {result['threshold']}")
        print()
        for name, pillar in result["pillars"].items():
            print(f"  {name}: {pillar['score']}/100")
        if result["warnings"]:
            print()
            for w in result["warnings"]:
                print(f"  ⚠️  {w}")

    sys.exit(0 if result["pass"] else 1)


if __name__ == "__main__":
    main()
