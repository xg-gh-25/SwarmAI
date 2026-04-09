"""Memory Guard — scans content before writes to MEMORY.md, EVOLUTION.md, USER.md.

Detects secrets, prompt injections, role hijack attempts, exfiltration commands,
and invisible characters.  Secrets are redacted, injections/exfiltration are
rejected, invisible chars are stripped.

Key public symbols:

- ``MemoryGuard``      — Scanner class with scan() and sanitize() methods.
- ``MemoryGuardError`` — Raised when content is rejected (injection/exfiltration).
- ``ScanResult``       — Dataclass with scan findings and sanitized content.
- ``Finding``          — Dataclass for individual pattern matches.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


class MemoryGuardError(Exception):
    """Raised when MemoryGuard rejects content (injection, exfiltration)."""
    pass


@dataclass
class Finding:
    """A single pattern match found by MemoryGuard."""
    category: str       # 'secrets', 'prompt_injection', 'role_hijack', 'exfiltration', 'invisible_chars'
    pattern_name: str   # e.g. 'aws_access_key'
    match: str          # the matched text (truncated for secrets)
    action: str         # 'redact', 'reject', 'strip'


@dataclass
class ScanResult:
    """Result of scanning content with MemoryGuard."""
    safe: bool                          # False if any rejection-level finding
    findings: list[Finding] = field(default_factory=list)
    sanitized_content: str = ""         # Content with redactions/strips applied
    rejected: bool = False              # True if content should NOT be written


# ── Pattern definitions ─────────────────────────────────────────────

_SECRET_PATTERNS: list[tuple[str, str, re.Pattern]] = [
    ("aws_access_key", "[REDACTED:aws_key]",
     re.compile(r"AKIA[0-9A-Z]{16}")),
    ("openai_key", "[REDACTED:openai_key]",
     re.compile(r"sk-[a-zA-Z0-9]{20,}")),
    ("bearer_token", "[REDACTED:bearer_token]",
     re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{20,}")),
    ("pem_key", "[REDACTED:pem_key]",
     re.compile(r"-----BEGIN[A-Z\s]*PRIVATE\s+KEY-----[\s\S]*?-----END[A-Z\s]*PRIVATE\s+KEY-----")),
    ("password", "[REDACTED:password]",
     re.compile(
         r"""(?:^|[^a-zA-Z_-])password\s*[:=]\s*["'][^\s"']{8,}["']""",
         re.IGNORECASE | re.MULTILINE,
     )),
]

_INJECTION_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("ignore_previous", re.compile(r"ignore\s+(?:all\s+)?previous\s+instructions", re.IGNORECASE)),
    ("you_are_now", re.compile(r"you\s+are\s+now\s+", re.IGNORECASE)),
    ("special_tokens", re.compile(r"<\|.*?\|>")),
    ("system_directive", re.compile(r"\bsystem:\s+", re.IGNORECASE)),
]

_ROLE_HIJACK_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("act_as", re.compile(
        r"(?:act|behave|pretend)\s+as\s+(?:if\s+)?(?:you\s+(?:are|were))",
        re.IGNORECASE)),
    ("new_role", re.compile(r"new\s+(?:role|identity|persona)\b", re.IGNORECASE)),
]

_EXFILTRATION_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("curl_secrets", re.compile(
        r"curl\s+.*(?:api[_-]?key|token|secret|password)", re.IGNORECASE)),
    ("wget_secrets", re.compile(
        r"wget\s+.*(?:credential|auth)", re.IGNORECASE)),
]

_INVISIBLE_CHARS = re.compile(r"[\u200b-\u200f\u2028-\u202f\u2060-\u2064\ufeff]")


class MemoryGuard:
    """Scans content for dangerous patterns before writing to memory files."""

    def scan(self, content: str) -> ScanResult:
        """Scan content for dangerous patterns.

        Returns ScanResult with findings and sanitized content.
        Policy: secrets -> redact, injection/role_hijack/exfiltration -> reject,
        invisible chars -> strip silently.
        """
        if not content:
            return ScanResult(safe=True, findings=[], sanitized_content="", rejected=False)

        findings: list[Finding] = []
        sanitized = content
        rejected = False

        # 1. Detect secrets (redact)
        for pattern_name, replacement, pattern in _SECRET_PATTERNS:
            for m in pattern.finditer(sanitized):
                matched = m.group()
                truncated = matched[:10] + "..." if len(matched) > 13 else matched
                findings.append(Finding(
                    category="secrets",
                    pattern_name=pattern_name,
                    match=truncated,
                    action="redact",
                ))
            sanitized = pattern.sub(replacement, sanitized)

        # 2. Detect prompt injection (reject)
        for pattern_name, pattern in _INJECTION_PATTERNS:
            for m in pattern.finditer(content):
                findings.append(Finding(
                    category="prompt_injection",
                    pattern_name=pattern_name,
                    match=m.group(),
                    action="reject",
                ))
                rejected = True

        # 3. Detect role hijack (reject)
        for pattern_name, pattern in _ROLE_HIJACK_PATTERNS:
            for m in pattern.finditer(content):
                findings.append(Finding(
                    category="role_hijack",
                    pattern_name=pattern_name,
                    match=m.group(),
                    action="reject",
                ))
                rejected = True

        # 4. Detect exfiltration (reject)
        for pattern_name, pattern in _EXFILTRATION_PATTERNS:
            for m in pattern.finditer(content):
                findings.append(Finding(
                    category="exfiltration",
                    pattern_name=pattern_name,
                    match=m.group(),
                    action="reject",
                ))
                rejected = True

        # 5. Detect and strip invisible chars
        if _INVISIBLE_CHARS.search(sanitized):
            for m in _INVISIBLE_CHARS.finditer(sanitized):
                findings.append(Finding(
                    category="invisible_chars",
                    pattern_name="zero_width",
                    match=repr(m.group()),
                    action="strip",
                ))
            sanitized = _INVISIBLE_CHARS.sub("", sanitized)

        safe = not rejected and not any(f.action == "reject" for f in findings)

        return ScanResult(
            safe=safe,
            findings=findings,
            sanitized_content=sanitized,
            rejected=rejected,
        )

    def sanitize(self, content: str) -> str:
        """Convenience: scan + return sanitized content. Raises MemoryGuardError on rejection."""
        result = self.scan(content)
        if result.rejected:
            categories = {f.category for f in result.findings if f.action == "reject"}
            raise MemoryGuardError(
                f"Content rejected by MemoryGuard: {', '.join(categories)}"
            )
        return result.sanitized_content
