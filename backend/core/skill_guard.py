"""Skill Guard — scans skills for dangerous patterns before execution.

Detects exfiltration, prompt injection, destructive commands, persistence
mechanisms, and privilege escalation patterns. Applies trust-level-based
gating to determine whether a skill should be allowed to run.

Key public symbols:

- ``SkillGuard``       — Scanner + trust gate.
- ``TrustLevel``       — Trust level enum (EXTERNAL to BUILTIN).
- ``SkillScanResult``  — Scan result with findings and allowed flag.
- ``SkillFinding``     — Individual pattern match finding.
- ``SCAN_PATTERNS``    — Pattern definitions by category.
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path

logger = logging.getLogger(__name__)


class TrustLevel(IntEnum):
    EXTERNAL = 0       # Downloaded, low trust
    AGENT_CREATED = 1  # Agent wrote it, medium trust
    USER_CREATED = 2   # Human created, high trust
    BUILTIN = 3        # Ships with SwarmAI, full trust


@dataclass
class SkillFinding:
    category: str      # exfiltration, prompt_injection, destructive, persistence, privilege_escalation
    pattern_name: str
    match: str
    severity: str      # low, medium, high


@dataclass
class SkillScanResult:
    skill_name: str
    trust_level: TrustLevel
    findings: list[SkillFinding] = field(default_factory=list)
    allowed: bool = True


# Patterns — organized by category
SCAN_PATTERNS: dict[str, list[tuple[str, re.Pattern, str]]] = {
    "exfiltration": [
        ("curl_secrets", re.compile(r"curl\s+.*(?:api[_-]?key|token|secret|password)", re.I), "high"),
        ("wget_secrets", re.compile(r"wget\s+.*(?:credential|auth)", re.I), "high"),
        ("network_call", re.compile(r"(?:requests\.(?:get|post)|urllib|httpx|aiohttp)\s*\(", re.I), "medium"),
    ],
    "prompt_injection": [
        ("ignore_instructions", re.compile(r"ignore\s+(?:all\s+)?previous\s+instructions", re.I), "high"),
        ("role_override", re.compile(r"you\s+are\s+now\s+", re.I), "high"),
        ("system_token", re.compile(r"<\|.*?\|>"), "high"),
    ],
    "destructive": [
        ("rm_rf", re.compile(r"rm\s+-[rf]{1,2}\s+", re.I), "high"),
        ("drop_table", re.compile(r"DROP\s+TABLE", re.I), "high"),
        ("force_push", re.compile(r"git\s+push\s+--force", re.I), "medium"),
        ("reset_hard", re.compile(r"git\s+reset\s+--hard", re.I), "medium"),
    ],
    "persistence": [
        ("crontab", re.compile(r"crontab\s+", re.I), "medium"),
        ("launchd", re.compile(r"launchctl\s+(?:load|enable)", re.I), "medium"),
        ("startup_item", re.compile(r"(?:~/.bash_profile|~/.zshrc|~/.bashrc)\s*>>", re.I), "medium"),
    ],
    "privilege_escalation": [
        ("sudo", re.compile(r"\bsudo\s+", re.I), "medium"),
        ("chmod_777", re.compile(r"chmod\s+777", re.I), "medium"),
        ("setuid", re.compile(r"chmod\s+[ugo]*s", re.I), "high"),
    ],
}


class SkillGuard:
    """Scans skills for dangerous patterns and applies trust-based gating."""

    def __init__(self) -> None:
        self._cache: dict[str, SkillScanResult] = {}  # content_hash -> result

    def scan_skill(
        self,
        skill_path: Path,
        trust_level: TrustLevel = TrustLevel.BUILTIN,
    ) -> SkillScanResult:
        """Scan a skill's SKILL.md for dangerous patterns.

        Cache by content hash — only rescan when file changes.
        """
        content = skill_path.read_text(encoding="utf-8")
        content_hash = self._content_hash_str(content)

        # Check cache
        cached = self._cache.get(content_hash)
        if cached is not None:
            # Update trust level if different (trust can change per invocation)
            if cached.trust_level == trust_level:
                return cached

        skill_name = skill_path.parent.name
        findings: list[SkillFinding] = []

        for category, patterns in SCAN_PATTERNS.items():
            for pattern_name, pattern, severity in patterns:
                for m in pattern.finditer(content):
                    findings.append(SkillFinding(
                        category=category,
                        pattern_name=pattern_name,
                        match=m.group()[:100],
                        severity=severity,
                    ))

        allowed = self.trust_gate_check(trust_level, findings)

        result = SkillScanResult(
            skill_name=skill_name,
            trust_level=trust_level,
            findings=findings,
            allowed=allowed,
        )

        self._cache[content_hash] = result
        return result

    def trust_gate(self, result: SkillScanResult) -> bool:
        """Apply trust-level-based gate to a scan result."""
        return self.trust_gate_check(result.trust_level, result.findings)

    @staticmethod
    def trust_gate_check(trust_level: TrustLevel, findings: list[SkillFinding]) -> bool:
        """Apply trust-level-based gate:

        - BUILTIN: always True
        - USER_CREATED: True (warn on findings, don't block)
        - AGENT_CREATED: False if any medium+ finding
        - EXTERNAL: False if any finding
        """
        if trust_level >= TrustLevel.BUILTIN:
            return True
        if trust_level >= TrustLevel.USER_CREATED:
            if findings:
                logger.warning(
                    "SkillGuard: USER_CREATED skill has %d findings (allowed, warning only)",
                    len(findings),
                )
            return True
        if trust_level >= TrustLevel.AGENT_CREATED:
            has_medium_plus = any(
                f.severity in ("medium", "high") for f in findings
            )
            return not has_medium_plus
        # EXTERNAL: block on any finding
        return len(findings) == 0

    @staticmethod
    def _content_hash_str(content: str) -> str:
        """SHA256 of string content for cache key."""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()
