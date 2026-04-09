"""Session transcript miner for building skill evaluation datasets.

Scans Claude Code session transcripts (JSONL) and SwarmAI message DB
to extract per-skill usage examples for automated optimization.

Key public symbols:
- ``EvalExample``       -- Single skill usage with prompt/outcome/correction.
- ``SessionMiner``      -- Mines transcripts and DB for skill-relevant examples.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class EvalExample:
    user_prompt: str
    skill_invoked: str
    agent_actions: str       # Summary of what agent did
    user_correction: str | None  # None if user accepted output
    final_outcome: str
    score: float             # 1.0 if accepted, 0.5 if corrected, 0.0 if abandoned


# Patterns indicating user correction
_CORRECTION_PATTERNS = re.compile(
    r"\b(?:no|don'?t|stop|wrong|incorrect|fix|undo|revert|instead|actually|wait)\b",
    re.IGNORECASE,
)

# Patterns indicating abandonment
_ABANDON_PATTERNS = re.compile(
    r"\b(?:stop|nevermind|never\s*mind|cancel|abort|forget\s*it)\b",
    re.IGNORECASE,
)


class SessionMiner:
    def __init__(self, transcripts_dir: Path, skills_dir: Path, evals_dir: Path) -> None:
        """
        Args:
            transcripts_dir: Path to directory with JSONL transcript files.
            skills_dir: Path to backend/skills/ with s_*/SKILL.md.
            evals_dir: Path to write skill_evals/{skill_name}.jsonl output.
        """
        self._transcripts_dir = transcripts_dir
        self._skills_dir = skills_dir
        self._evals_dir = evals_dir
        self._guard = None  # Lazy-init MemoryGuard for secret scrubbing

    def _load_skill_keywords(self, skill_name: str) -> list[str]:
        """Extract TRIGGER keywords from SKILL.md description field."""
        skill_path = self._skills_dir / f"s_{skill_name}" / "SKILL.md"
        if not skill_path.exists():
            return [skill_name]

        try:
            content = skill_path.read_text(encoding="utf-8")
            # Find TRIGGER line in description
            trigger_match = re.search(r"TRIGGER:\s*(.+?)(?:\n|$)", content)
            if trigger_match:
                raw = trigger_match.group(1)
                keywords = [kw.strip().lower() for kw in raw.split(",") if kw.strip()]
                if keywords:
                    return keywords
        except Exception as exc:
            logger.warning("Failed to load keywords for %s: %s", skill_name, exc)

        return [skill_name]

    def _parse_transcript(self, path: Path) -> list[dict]:
        """Read JSONL file line by line, return list of user/assistant records."""
        records: list[dict] = []
        with open(path, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("Malformed JSON at %s:%d, skipping", path.name, line_num)
                    continue
                rtype = record.get("type", "")
                if rtype in ("user", "assistant"):
                    records.append(record)
        return records

    def _scrub_secrets(self, text: str) -> str:
        """Use MemoryGuard patterns to redact secrets from text (cached instance)."""
        try:
            if self._guard is None:
                from core.memory_guard import MemoryGuard
                self._guard = MemoryGuard()
            result = self._guard.scan(text)
            return result.sanitized_content
        except ImportError:
            return text
        except Exception:
            return text

    def _get_message_content(self, record: dict) -> str:
        """Extract text content from a transcript record."""
        msg = record.get("message", {})
        content = msg.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        parts.append(block.get("text", ""))
                    elif block.get("type") == "tool_use":
                        parts.append(f"[tool:{block.get('name', '?')}]")
                elif isinstance(block, str):
                    parts.append(block)
            return " ".join(parts)
        return str(content)

    def _extract_skill_invocations(
        self, records: list[dict], skill_name: str, keywords: list[str]
    ) -> list[EvalExample]:
        """Find sequences where a skill was invoked."""
        examples: list[EvalExample] = []
        kw_pattern = re.compile(
            r"\b(?:" + "|".join(re.escape(kw) for kw in keywords) + r")\b",
            re.IGNORECASE,
        )

        i = 0
        while i < len(records):
            record = records[i]
            if record.get("type") != "user":
                i += 1
                continue

            user_text = self._get_message_content(record)
            if not kw_pattern.search(user_text):
                i += 1
                continue

            # Found a user message matching skill keywords.
            # Collect assistant response(s) immediately following.
            agent_text = ""
            j = i + 1
            while j < len(records) and records[j].get("type") == "assistant":
                agent_text += self._get_message_content(records[j]) + " "
                j += 1
            agent_text = agent_text.strip()

            # Find the next user message for correction/abandonment detection.
            # Skip any intervening assistant messages (tool use sequences).
            user_correction = None
            score = 1.0
            next_user_idx = j  # j already points past assistant messages
            if next_user_idx < len(records) and records[next_user_idx].get("type") == "user":
                next_text = self._get_message_content(records[next_user_idx])
                if _ABANDON_PATTERNS.search(next_text):
                    user_correction = next_text
                    score = 0.0
                elif _CORRECTION_PATTERNS.search(next_text):
                    user_correction = next_text
                    score = 0.5

            examples.append(EvalExample(
                user_prompt=user_text,
                skill_invoked=skill_name,
                agent_actions=agent_text[:500],  # truncate
                user_correction=user_correction,
                final_outcome="completed" if score > 0 else "abandoned",
                score=score,
            ))

            # Skip past the matched sequence
            i = next_user_idx + 1 if next_user_idx < len(records) else i + 1
            continue

        return examples

    def mine_for_skill(self, skill_name: str) -> list[EvalExample]:
        """Mine all transcripts for a specific skill. Returns eval examples."""
        keywords = self._load_skill_keywords(skill_name)
        examples: list[EvalExample] = []
        if self._transcripts_dir.exists():
            for jsonl in sorted(self._transcripts_dir.glob("*.jsonl")):
                try:
                    records = self._parse_transcript(jsonl)
                    examples.extend(
                        self._extract_skill_invocations(records, skill_name, keywords)
                    )
                except Exception as e:
                    logger.warning("Failed to parse %s: %s", jsonl.name, e)

        # Scrub secrets
        for ex in examples:
            ex.user_prompt = self._scrub_secrets(ex.user_prompt)
            ex.agent_actions = self._scrub_secrets(ex.agent_actions)
            if ex.user_correction:
                ex.user_correction = self._scrub_secrets(ex.user_correction)

        return examples

    def mine_all(self) -> dict[str, list[EvalExample]]:
        """Mine for all skills. Returns {skill_name: [examples]}."""
        results: dict[str, list[EvalExample]] = {}
        if self._skills_dir.exists():
            for skill_dir in sorted(self._skills_dir.iterdir()):
                if skill_dir.is_dir() and skill_dir.name.startswith("s_"):
                    name = skill_dir.name[2:]  # strip s_ prefix
                    examples = self.mine_for_skill(name)
                    if examples:
                        results[name] = examples
        return results

    def get_eligible_skills(self, min_examples: int = 5) -> list[str]:
        """Return skill names with >= min_examples eval examples."""
        all_examples = self.mine_all()
        return [name for name, exs in all_examples.items() if len(exs) >= min_examples]

    def save_evals(self, skill_name: str, examples: list[EvalExample]) -> Path:
        """Save eval examples to skill_evals/{skill_name}.jsonl."""
        self._evals_dir.mkdir(parents=True, exist_ok=True)
        path = self._evals_dir / f"{skill_name}.jsonl"
        with open(path, "w") as f:
            for ex in examples:
                f.write(json.dumps(asdict(ex)) + "\n")
        return path
