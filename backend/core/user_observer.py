"""User Observer — extracts behavioral observations from session messages.

Detects user preferences, corrections, expertise indicators, and language
preferences by analyzing session messages. Observations are stored in JSONL
format and used to improve personalization over time.

Key public symbols:

- ``Observation``    — Dataclass representing a single observation.
- ``UserObserver``   — Core observation logic: detect, consolidate, persist.
- ``CORRECTION_PATTERNS`` — Compiled regexes for explicit user corrections.
- ``EXPERTISE_KEYWORDS``  — Keyword sets for expertise detection.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Observation:
    date: str              # YYYY-MM-DD
    category: str          # e.g. "preferences.communication"
    observation: str       # Human-readable description
    confidence: float      # 0.0-1.0
    source_session: str    # session_id
    evidence: str          # What triggered this observation


CORRECTION_PATTERNS = [
    re.compile(r"(?:don'?t|stop|never|please\s+don'?t)\s+(.{10,80})", re.I),
    re.compile(r"(?:i\s+prefer|instead\s+of|rather\s+than)\s+(.{10,80})", re.I),
]

EXPERTISE_KEYWORDS: dict[str, list[str]] = {
    "domains": ["architecture", "distributed", "kubernetes", "terraform", "security", "ml", "nlp"],
    "tools": ["pytest", "vitest", "docker", "git", "webpack", "vite", "tailwind"],
    "languages": ["python", "typescript", "rust", "go", "java", "sql"],
}

# East Asian script detection: CJK Unified Ideographs (Chinese),
# Hiragana + Katakana (Japanese), Hangul Syllables (Korean).
_EAST_ASIAN_PATTERN = re.compile(
    r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff"   # CJK Unified Ideographs
    r"\u3040-\u309f\u30a0-\u30ff"                   # Hiragana + Katakana
    r"\uac00-\ud7af]"                               # Hangul Syllables
)


class UserObserver:
    """Extracts behavioral observations from session messages."""

    def __init__(self, observations_path: Path) -> None:
        self._path = observations_path

    def observe_session(self, messages: list[dict], session_id: str) -> list[Observation]:
        """Extract observations from session messages.

        Each message dict has: role (str), content (str).
        Only analyze 'user' role messages for preferences/corrections.
        Analyze both 'user' and 'assistant' for expertise detection.

        Detection strategies:
        1. Explicit corrections: match CORRECTION_PATTERNS on user messages
        2. Expertise indicators: count EXPERTISE_KEYWORDS matches
        3. Language preference: detect Chinese characters in user messages
        """
        if not messages:
            return []

        observations: list[Observation] = []
        today = datetime.now().strftime("%Y-%m-%d")
        user_messages = [m for m in messages if m.get("role") == "user"]
        all_text = " ".join(m.get("content", "") for m in messages)

        # 1. Explicit corrections (user messages only)
        for msg in user_messages:
            content = msg.get("content", "")
            for pattern in CORRECTION_PATTERNS:
                match = pattern.search(content)
                if match:
                    observations.append(Observation(
                        date=today,
                        category="corrections",
                        observation=f"User correction: {match.group(0)[:60]}",
                        confidence=0.9,
                        source_session=session_id,
                        evidence=content[:120],
                    ))

        # 2. Expertise indicators (all messages)
        for subcategory, keywords in EXPERTISE_KEYWORDS.items():
            count = 0
            matched_kw: list[str] = []
            for kw in keywords:
                if re.search(rf"\b{re.escape(kw)}\b", all_text, re.I):
                    count += 1
                    matched_kw.append(kw)
            if count >= 3:
                observations.append(Observation(
                    date=today,
                    category=f"expertise.{subcategory}",
                    observation=f"Expertise in {subcategory}: {', '.join(matched_kw)}",
                    confidence=0.7,
                    source_session=session_id,
                    evidence=f"Keywords found: {', '.join(matched_kw)}",
                ))

        # 3. Language preference (user messages only)
        if user_messages:
            cjk_count = sum(1 for m in user_messages if _EAST_ASIAN_PATTERN.search(m.get("content", "")))
            ratio = cjk_count / len(user_messages)
            if ratio > 0.3:
                observations.append(Observation(
                    date=today,
                    category="preferences.communication",
                    observation="User frequently communicates in an East Asian language",
                    confidence=0.8,
                    source_session=session_id,
                    evidence=f"{cjk_count}/{len(user_messages)} messages contain CJK/Kana/Hangul characters",
                ))

        return observations

    def load_existing(self) -> list[Observation]:
        """Load existing observations from JSONL file."""
        if not self._path.exists():
            return []
        observations: list[Observation] = []
        try:
            for line in self._path.read_text(encoding="utf-8").strip().splitlines():
                if line.strip():
                    data = json.loads(line)
                    observations.append(Observation(**data))
        except Exception as exc:
            logger.warning("Failed to load observations from %s: %s", self._path, exc)
        return observations

    def save_observations(self, observations: list[Observation]) -> None:
        """Append new observations to JSONL file."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as f:
            for obs in observations:
                f.write(json.dumps(asdict(obs), ensure_ascii=False) + "\n")

    def consolidate(self, new: list[Observation], existing: list[Observation]) -> list[Observation]:
        """Merge new with existing. Same category+observation keeps newer with higher confidence."""
        # Build a map keyed by (category, observation)
        merged: dict[tuple[str, str], Observation] = {}
        for obs in existing:
            key = (obs.category, obs.observation)
            merged[key] = obs
        for obs in new:
            key = (obs.category, obs.observation)
            if key in merged:
                old = merged[key]
                # Keep the one with higher confidence; tie-break by date (newer wins)
                if obs.confidence > old.confidence or (obs.confidence == old.confidence and obs.date >= old.date):
                    merged[key] = obs
            else:
                merged[key] = obs
        return list(merged.values())

    def suggest_user_md_updates(self, observations: list[Observation]) -> list[str]:
        """After 5+ observations, suggest max 2 USER.md updates.

        Only suggest for categories with 3+ observations (pattern, not one-off).
        """
        if len(observations) < 5:
            return []

        # Count observations per category
        category_counts: dict[str, list[Observation]] = {}
        for obs in observations:
            category_counts.setdefault(obs.category, []).append(obs)

        suggestions: list[str] = []
        for category, obs_list in sorted(category_counts.items(), key=lambda x: -len(x[1])):
            if len(obs_list) >= 3:
                top_obs = sorted(obs_list, key=lambda o: -o.confidence)[:3]
                summary = "; ".join(o.observation for o in top_obs)
                suggestions.append(
                    f"[{category}] Pattern detected ({len(obs_list)} observations): {summary}"
                )
            if len(suggestions) >= 2:
                break

        return suggestions
