"""Memory content validation — thin wrapper around MemoryGuard.

MEMORY.md is injected into every future session's system prompt. A poisoned
memory entry affects ALL future conversations — worse than a single-session
prompt injection. This module validates content before it's written.

All detection patterns now live in ``memory_guard.py`` (single source of truth).
This module re-exports a convenience function for callers that don't need
the full ScanResult — notably ``locked_write.py`` which calls
``validate_memory_content(text)`` and checks the (bool, str|None) return.

Public symbols:

- ``validate_memory_content``  — Check text against known injection patterns.
- ``INJECTION_PATTERNS``       — Re-exported from MemoryGuard (for testing).
"""

import logging

logger = logging.getLogger(__name__)


def validate_memory_content(text: str) -> tuple[bool, str | None]:
    """Check text for prompt injection patterns.

    Delegates to MemoryGuard.scan() — all patterns defined there.

    Args:
        text: Content about to be written to MEMORY.md.

    Returns:
        (True, None) if safe.
        (False, pattern_name) if an injection pattern was detected.
    """
    if not text or not text.strip():
        return (True, None)

    try:
        from core.memory_guard import MemoryGuard

        result = MemoryGuard().scan(text)
        if result.rejected:
            # Return the first rejection-level finding's pattern_name
            for finding in result.findings:
                if finding.action == "reject":
                    logger.warning(
                        "Memory injection blocked — pattern '%s' matched in: %.100s",
                        finding.pattern_name, text,
                    )
                    return (False, finding.pattern_name)
            # Shouldn't reach here if rejected=True, but safety fallback
            return (False, "unknown_rejection")

        return (True, None)

    except ImportError:
        # Standalone CLI mode — memory_guard not available.
        # Fall through to allow write (locked_write has its own guard).
        logger.debug("memory_guard not importable — skipping validation")
        return (True, None)


# Re-export for test inspection (legacy compatibility)
try:
    from core.memory_guard import _INJECTION_PATTERNS as _guard_patterns
    INJECTION_PATTERNS = _guard_patterns
except ImportError:
    INJECTION_PATTERNS = []
