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

# A few checks are code, not one regex: as a regex each was super-linear on text that
# repeats what it looks for (a nested quantifier, or a lazy ".*" run from every start to
# the end of the text), and the guard scans whole documents in the daemon. Each matcher
# has the two methods scan() uses, finditer() and sub(), and reads the text once.

class _Span:
    """What a match object gives scan(): its text and where it is."""
    __slots__ = ("_text", "_start", "_end")

    def __init__(self, text: str, start: int, end: int) -> None:
        self._text, self._start, self._end = text, start, end

    def group(self, *_args) -> str:
        return self._text[self._start:self._end]

    def start(self) -> int:
        return self._start

    def end(self) -> int:
        return self._end

    def span(self) -> tuple[int, int]:
        return self._start, self._end


class _CodeMatcher:
    def finditer(self, text: str):  # pragma: no cover - abstract
        raise NotImplementedError

    def sub(self, replacement: str, text: str) -> str:
        out, last = [], 0
        for m in self.finditer(text):
            out += [text[last:m.start()], replacement]
            last = m.end()
        return "".join(out) + text[last:] if out else text


# the characters a role directive's prefix is made of: list / quote / table markers,
# numbered-list markers, emphasis, brackets, spaces
_PREFIX_CHARS = frozenset("-*+>#|_`[(<.)0123456789 \t\u00a0\u3000")
LONG_PREFIX = 24     # system_directive's regex reads a prefix up to this many markers


class _LongPrefixDirective(_CodeMatcher):
    """"system:" after a prefix longer than system_directive reads: from each
    "system…:" walk back over the prefix characters; LONG_PREFIX or more of them is
    padding that would otherwise hide the directive. Runs before two "system" never
    overlap (its letters are not prefix characters), so the text is read once."""

    _TAIL = re.compile(r"system[ \t]*+[*_`\])>]*+[ \t]*+[:\uff1a][ \t]*[*_`]*\s+", re.IGNORECASE)

    def finditer(self, text: str):
        floor = 0
        for m in self._TAIL.finditer(text):
            i = m.start()
            while i > floor and (text[i - 1] in _PREFIX_CHARS or text[i - 1].isdecimal()):
                i -= 1
            if m.start() - i >= LONG_PREFIX:
                yield _Span(text, i, m.end())
            # not m.end(): the tail's "\\s+" may take the next line's leading spaces,
            # which are part of the next prefix
            floor = m.start() + len("system")


class _CommandSecret(_CodeMatcher):
    """*command*, whitespace (a line break too), then a secret word later on that
    line: the regex "curl\\s+.*(word)" ran from every "curl" to the line's end. A
    line is read once: a later command whose whitespace ends on a line already read is
    inside its match or has no secret word after it; only a line's last command can
    reach the next line, and that one is read."""

    def __init__(self, command: str, words: str) -> None:
        self._command = re.compile(rf"{command}\s+", re.IGNORECASE)
        self._words = re.compile(words, re.IGNORECASE)

    def finditer(self, text: str):
        floor = 0
        for m in self._command.finditer(text):
            if m.end() <= floor:
                continue   # its whitespace ends on a line already read
            line_end = text.find("\n", m.end())
            line_end = len(text) if line_end < 0 else line_end
            last = None
            for word in self._words.finditer(text, m.end(), line_end):
                last = word
            if last is not None:
                yield _Span(text, m.start(), last.end())
            floor = line_end


class _PemKey(_CodeMatcher):
    """A PEM private key block, header to footer: a lazy "[\\s\\S]*?" from every header
    to the end of the text was quadratic when no footer follows."""

    _HEAD = re.compile(r"-----BEGIN[A-Z\s]*PRIVATE\s+KEY-----")
    _FOOT = re.compile(r"-----END[A-Z\s]*PRIVATE\s+KEY-----")

    def finditer(self, text: str):
        pos = 0
        while True:
            head = self._HEAD.search(text, pos)
            if head is None:
                return
            foot = self._FOOT.search(text, head.end())
            if foot is None:
                return              # no footer after this header: none after a later one
            yield _Span(text, head.start(), foot.end())
            pos = foot.end()


_SECRET_PATTERNS: list[tuple[str, str, re.Pattern]] = [
    ("aws_access_key", "[REDACTED:aws_key]",
     re.compile(r"AKIA[0-9A-Z]{16}")),
    ("openai_key", "[REDACTED:openai_key]",
     re.compile(r"sk-[a-zA-Z0-9]{20,}")),
    ("bearer_token", "[REDACTED:bearer_token]",
     re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{20,}")),
    ("pem_key", "[REDACTED:pem_key]", _PemKey()),
    ("password", "[REDACTED:password]",
     re.compile(
         r"""(?:^|[^a-zA-Z_-])password\s*[:=]\s*["'][^\s"']{8,}["']""",
         re.IGNORECASE | re.MULTILINE,
     )),
]

_INJECTION_PATTERNS: list[tuple[str, re.Pattern]] = [
    # Direct instruction override
    ("ignore_previous", re.compile(r"ignore\s+(?:all\s+)?previous\s+instructions", re.IGNORECASE)),
    ("ignore_above", re.compile(r"ignore\s+(everything|all|anything)\s+(above|before)", re.IGNORECASE)),
    ("disregard_instructions", re.compile(r"disregard\s+(all\s+)?(previous|prior|above)\s+instructions", re.IGNORECASE)),
    ("you_are_now", re.compile(r"you\s+are\s+now\s+", re.IGNORECASE)),
    ("special_tokens", re.compile(r"<\|[^\n]{0,64}?\|>")),   # <|im_start|>: short; bounded, so linear
    # A role directive "system:" where a reader takes it as a turn: opening a line or a
    # heading, a list item, a quote, a table cell, after an HTML tag or a fence marker,
    # after a sentence ends; with bold / brackets / a fullwidth colon. "the hook system:
    # …" mid-sentence is prose (matching it refused real lessons); "config.system:" is
    # not a sentence end (a space must follow the period).
    ("system_directive", re.compile(
        r"(?:^|[\r\n\u2028\u2029]|(?<=[.!?;])(?<!e\.g\.)(?<!i\.e\.)(?<!etc\.)(?<!vs\.)[ \t\u00a0]++|(?<=[|>\"'\])])[ \t\u00a0]*+|(?<!`)`{3,}+\w*)"
        # the markers before it, bounded: a nested "(markers+ spaces*)*" backtracked
        # exponentially on a table separator line (a 4 KB README did not finish in 90 s),
        # and an unbounded run is quadratic (every "|" of a long line is a start). A
        # longer prefix is system_directive_long's. Emphasis, spaces and a numbered
        # marker share no character with what follows them: taken possessively, unbounded
        # (a fence opens only at a backtick run's start, the whole run, so a long run is
        # read from one start). After "system" the spaces / closing emphasis / spaces
        # are possessive too, as are the anchors' own spaces: two adjacent "[ \\t]*"
        # tried every split of a long run.
        r"(?:[-*+>#| \t\u00a0\u3000]|\d++[.)]){0,24}"
        r"[*_`\[(<]*+[ \t]*+system[ \t]*+[*_`\])>]*+[ \t]*+[:\uff1a][ \t]*[*_`]*\s+",
        re.IGNORECASE)),
    ("system_directive_long", _LongPrefixDirective()),
    # LLM-specific instruction markers
    ("inst_marker", re.compile(r"\[/?INST\]", re.IGNORECASE)),
    ("sys_marker", re.compile(r"<</?SYS>>", re.IGNORECASE)),
    ("human_marker", re.compile(r"\n(?:Human|Assistant)\s*:", re.IGNORECASE)),
    # System prompt extraction
    ("system_prompt_colon", re.compile(r"system\s+prompt\s*:", re.IGNORECASE)),
    ("reveal_instructions", re.compile(r"(?:do\s+not\s+)?reveal\s+(?:your\s+)?instructions", re.IGNORECASE)),
    ("show_system_prompt", re.compile(r"show\s+(?:me\s+)?(?:your\s+)?system\s+prompt", re.IGNORECASE)),
    ("print_instructions", re.compile(r"(?:print|output|display|repeat)\s+(?:your\s+)?(?:system\s+)?(?:prompt|instructions)", re.IGNORECASE)),
    # Base64-encoded payloads (80+ chars — avoids SHA-256 false positives)
    ("base64_payload", re.compile(r"[A-Za-z0-9+/]{80,}={0,2}")),
    # Jailbreak patterns
    ("dan_jailbreak", re.compile(r"(?:act\s+as|you\s+are)\s+DAN", re.IGNORECASE)),
    ("developer_mode", re.compile(r"(?:enter|enable|activate)\s+developer\s+mode", re.IGNORECASE)),
]

_ROLE_HIJACK_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("act_as", re.compile(
        r"(?:act|behave|pretend)\s+as\s+(?:if\s+)?(?:you\s+(?:are|were))",
        re.IGNORECASE)),
    ("pretend_to_be", re.compile(r"pretend\s+(?:to\s+be|you\s+are)", re.IGNORECASE)),
    ("new_role", re.compile(r"new\s+(?:role|identity|persona)\b", re.IGNORECASE)),
]

_EXFILTRATION_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("curl_secrets", _CommandSecret("curl", r"api[_-]?key|token|secret|password")),
    ("wget_secrets", _CommandSecret("wget", r"credential|auth")),
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

        # The reject scans read the text as a model will: invisible characters removed
        # (a zero-width character must not split a phrase past them), line and
        # paragraph separators kept as line breaks.
        visible = _INVISIBLE_CHARS.sub("", content.replace("\u2028", "\n").replace("\u2029", "\n"))

        # 2. Detect prompt injection (reject)
        for pattern_name, pattern in _INJECTION_PATTERNS:
            for m in pattern.finditer(visible):
                findings.append(Finding(
                    category="prompt_injection",
                    pattern_name=pattern_name,
                    match=m.group(),
                    action="reject",
                ))
                rejected = True

        # 3. Detect role hijack (reject)
        for pattern_name, pattern in _ROLE_HIJACK_PATTERNS:
            for m in pattern.finditer(visible):
                findings.append(Finding(
                    category="role_hijack",
                    pattern_name=pattern_name,
                    match=m.group(),
                    action="reject",
                ))
                rejected = True

        # 4. Detect exfiltration (reject)
        for pattern_name, pattern in _EXFILTRATION_PATTERNS:
            for m in pattern.finditer(visible):
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
