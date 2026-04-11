"""Session transcript miner for building skill evaluation datasets.

Scans Claude Code session transcripts (JSONL) and SwarmAI message DB
to extract per-skill usage examples for automated optimization.

Key public symbols:
- ``EvalExample``       -- Single skill usage with prompt/outcome/correction.
- ``SessionMiner``      -- Mines transcripts and DB for skill-relevant examples.

Performance notes (v1.4.0+):
- ``mine_all()`` uses single-pass parsing: each transcript read once,
  all skill patterns matched against the parsed records.
  Before: O(skills × files) = 56 × 1135 file reads.
  After:  O(files) = 1135 file reads.
- mtime filter: ``max_age_days`` (default 90) skips ancient transcripts.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from core.extraction_patterns import CORRECTION_PATTERNS as _CORRECTION_PATTERNS

# Default maximum age for transcript files to process.
# 90 days balances coverage vs. performance as the corpus grows.
MAX_AGE_DAYS = 90

logger = logging.getLogger(__name__)


@dataclass
class EvalExample:
    user_prompt: str
    skill_invoked: str
    agent_actions: str       # Summary of what agent did
    user_correction: str | None  # None if user accepted output
    final_outcome: str
    score: float             # 1.0 if accepted, 0.5 if corrected, 0.0 if abandoned

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

    def _is_tool_result_content(self, content: object) -> bool:
        """Check if content is a list of tool_result blocks (SDK-generated, not real user input)."""
        if not isinstance(content, list):
            return False
        return all(
            isinstance(block, dict) and block.get("type") == "tool_result"
            for block in content
        )

    def _get_message_content(self, record: dict) -> str:
        """Extract text content from a transcript record.

        Handles both string content (user messages) and list content
        (assistant messages with text/tool_use blocks, or user messages
        with tool_result blocks).
        """
        msg = record.get("message", {})
        content = msg.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            # tool_result lists are SDK-generated, return empty
            if self._is_tool_result_content(content):
                return ""
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

    def _find_next_real_user_message(self, records: list[dict], start: int) -> int | None:
        """Find the next user message that is real text (not a tool_result list).

        Scans from ``start`` forward, skipping assistant messages and user
        messages whose content is a tool_result list (SDK-generated).

        Returns the index of the next real user message, or None.
        """
        for idx in range(start, len(records)):
            rec = records[idx]
            if rec.get("type") != "user":
                continue
            msg = rec.get("message", {})
            content = msg.get("content", "")
            # Skip tool_result lists
            if self._is_tool_result_content(content):
                continue
            # Skip empty string content
            if isinstance(content, str) and content.strip():
                return idx
        return None

    def _detect_correction(self, records: list[dict], after_idx: int) -> tuple[str | None, float]:
        """Detect user correction/abandonment in the next real user message after ``after_idx``.

        Returns (correction_text | None, score).
        """
        next_idx = self._find_next_real_user_message(records, after_idx)
        if next_idx is None:
            return None, 1.0

        next_text = self._get_message_content(records[next_idx])
        if _ABANDON_PATTERNS.search(next_text):
            return next_text, 0.0
        if _CORRECTION_PATTERNS.search(next_text):
            return next_text, 0.5
        return None, 1.0

    def _extract_skill_from_tool_use(self, block: dict) -> str | None:
        """Extract skill name from a tool_use block if it invokes the Skill tool.

        Returns the skill name (with ``s_`` prefix stripped) or None.
        """
        if not isinstance(block, dict):
            return None
        if block.get("type") != "tool_use" or block.get("name") != "Skill":
            return None
        inp = block.get("input", {})
        skill = inp.get("skill", "")
        if not skill:
            return None
        # Strip s_ prefix if present
        if skill.startswith("s_"):
            skill = skill[2:]
        return skill

    def _extract_skill_invocations(
        self, records: list[dict], skill_name: str, keywords: list[str]
    ) -> list[EvalExample]:
        """Find sequences where a skill was invoked.

        Detects skill invocations two ways:
        1. **Keyword match** -- user message text matches skill keywords.
        2. **Tool use match** -- assistant message contains a ``tool_use`` block
           with ``name == "Skill"`` and ``input.skill`` matching ``skill_name``.

        For correction detection, scans for the next *real* user message
        (skipping tool_result lists which are SDK-generated).
        """
        examples: list[EvalExample] = []
        seen_indices: set[int] = set()  # avoid duplicate examples

        kw_pattern = re.compile(
            r"\b(?:" + "|".join(re.escape(kw) for kw in keywords) + r")\b",
            re.IGNORECASE,
        )

        i = 0
        while i < len(records):
            record = records[i]

            # --- Path 1: keyword match in user message ---
            if record.get("type") == "user" and i not in seen_indices:
                user_text = self._get_message_content(record)
                if user_text and kw_pattern.search(user_text):
                    # Collect assistant response(s) immediately following
                    agent_text = ""
                    j = i + 1
                    while j < len(records) and records[j].get("type") == "assistant":
                        agent_text += self._get_message_content(records[j]) + " "
                        j += 1
                    agent_text = agent_text.strip()

                    user_correction, score = self._detect_correction(records, j)

                    examples.append(EvalExample(
                        user_prompt=user_text,
                        skill_invoked=skill_name,
                        agent_actions=agent_text[:1500],
                        user_correction=user_correction,
                        final_outcome="completed" if score > 0 else "abandoned",
                        score=score,
                    ))
                    seen_indices.add(i)
                    i = j + 1 if j < len(records) else i + 1
                    continue

            # --- Path 2: tool_use block in assistant message ---
            if record.get("type") == "assistant" and i not in seen_indices:
                msg = record.get("message", {})
                content = msg.get("content", [])
                if isinstance(content, list):
                    for block in content:
                        invoked = self._extract_skill_from_tool_use(block)
                        if invoked == skill_name:
                            # Find the preceding user message for prompt context
                            user_text = ""
                            for k in range(i - 1, -1, -1):
                                if records[k].get("type") == "user":
                                    user_text = self._get_message_content(records[k])
                                    if user_text:
                                        break

                            # Collect text from this + following assistant messages
                            # (skill output often spans multiple assistant turns
                            # separated by tool_result round-trips)
                            agent_parts = []
                            for j in range(i, len(records)):
                                if records[j].get("type") == "assistant":
                                    text = self._get_message_content(records[j])
                                    if text:
                                        agent_parts.append(text)
                                elif records[j].get("type") == "user":
                                    uc = records[j].get("message", {}).get("content", "")
                                    if self._is_tool_result_content(uc):
                                        continue  # skip SDK tool_result, keep scanning
                                    break  # real user message = end of agent turn
                                else:
                                    break
                            agent_text = " ".join(agent_parts).strip()

                            user_correction, score = self._detect_correction(records, i + 1)

                            examples.append(EvalExample(
                                user_prompt=user_text,
                                skill_invoked=skill_name,
                                agent_actions=agent_text[:1500],
                                user_correction=user_correction,
                                final_outcome="completed" if score > 0 else "abandoned",
                                score=score,
                            ))
                            seen_indices.add(i)
                            break  # one example per assistant record

            i += 1

        return examples

    def _iter_transcripts(self, max_age_days: int = MAX_AGE_DAYS) -> list[Path]:
        """Return transcript files filtered by mtime, sorted oldest-first.

        Skips files older than ``max_age_days`` to bound I/O on large corpora.
        Pass ``max_age_days=0`` to disable filtering (process all files).
        """
        if not self._transcripts_dir.exists():
            return []

        all_files = list(self._transcripts_dir.glob("*.jsonl"))
        if max_age_days <= 0:
            return sorted(all_files)

        cutoff = time.time() - max_age_days * 86400
        recent = []
        for f in all_files:
            try:
                if f.stat().st_mtime > cutoff:
                    recent.append(f)
            except (FileNotFoundError, OSError):
                continue  # File deleted between glob() and stat()
        return sorted(recent)

    def mine_for_skill(
        self, skill_name: str, max_age_days: int = MAX_AGE_DAYS
    ) -> list[EvalExample]:
        """Mine transcripts for a specific skill. Returns eval examples."""
        keywords = self._load_skill_keywords(skill_name)
        examples: list[EvalExample] = []
        for jsonl in self._iter_transcripts(max_age_days):
            try:
                records = self._parse_transcript(jsonl)
                examples.extend(
                    self._extract_skill_invocations(records, skill_name, keywords)
                )
            except Exception as e:
                logger.warning("Failed to parse %s: %s", jsonl.name, e)

        self._scrub_examples(examples)
        return examples

    def mine_all(self, max_age_days: int = MAX_AGE_DAYS) -> dict[str, list[EvalExample]]:
        """Mine for all skills with **single-pass I/O** over transcripts.

        Previous approach: O(skills × files) file reads — each transcript
        read once per skill (56 skills × 1135 files = 63k reads of 632 MB).
        Now: O(files) file reads — each transcript parsed once, then matched
        against all skill keywords in memory. Matching is still O(files × skills)
        but the expensive I/O is eliminated.

        New approach: O(files) — each transcript parsed once, all skill
        patterns matched against the parsed records.

        Returns {skill_name: [examples]}.
        """
        # 1. Load all skill keywords upfront
        skill_keywords: dict[str, list[str]] = {}
        if self._skills_dir.exists():
            for skill_dir in sorted(self._skills_dir.iterdir()):
                if skill_dir.is_dir() and skill_dir.name.startswith("s_"):
                    name = skill_dir.name[2:]
                    skill_keywords[name] = self._load_skill_keywords(name)

        if not skill_keywords:
            return {}

        # 2. Single pass: parse each transcript once, extract for all skills
        results: dict[str, list[EvalExample]] = {}
        transcripts = self._iter_transcripts(max_age_days)
        logger.info(
            "SessionMiner: mining %d transcripts for %d skills",
            len(transcripts), len(skill_keywords),
        )

        for jsonl in transcripts:
            try:
                records = self._parse_transcript(jsonl)
            except Exception as e:
                logger.warning("Failed to parse %s: %s", jsonl.name, e)
                continue

            # Match every skill against the same parsed records
            for skill_name, keywords in skill_keywords.items():
                examples = self._extract_skill_invocations(
                    records, skill_name, keywords
                )
                if examples:
                    results.setdefault(skill_name, []).extend(examples)

        # 3. Scrub secrets across all results
        for examples in results.values():
            self._scrub_examples(examples)

        return results

    def _scrub_examples(self, examples: list[EvalExample]) -> None:
        """Scrub secrets from a list of eval examples in-place."""
        for ex in examples:
            ex.user_prompt = self._scrub_secrets(ex.user_prompt)
            ex.agent_actions = self._scrub_secrets(ex.agent_actions)
            if ex.user_correction:
                ex.user_correction = self._scrub_secrets(ex.user_correction)

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
