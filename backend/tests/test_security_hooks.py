"""Property-based tests for dangerous command detection.

# Feature: agent-code-refactoring, Property 1: Dangerous command detection equivalence

Uses Hypothesis to verify that check_dangerous_command correctly detects
dangerous patterns and returns consistent results. For any string containing
a dangerous pattern substring, the function returns a non-None reason. For
any string, calling the function twice returns the same result (determinism).

**Validates: Requirements 1.2**
"""

import re

import pytest
from hypothesis import given, strategies as st, settings

from core.security_hooks import DANGEROUS_PATTERNS, check_dangerous_command


PROPERTY_SETTINGS = settings(max_examples=100)

# Strategy: pick a random dangerous pattern tuple
_dangerous_pattern = st.sampled_from(DANGEROUS_PATTERNS)


class TestDangerousCommandDetection:
    """Property 1: Dangerous command detection equivalence.

    **Validates: Requirements 1.2**

    For any string cmd, check_dangerous_command(cmd) returns the matching
    reason string if any DANGEROUS_PATTERNS entry matches, or None otherwise.
    Results are deterministic across repeated calls.
    """

    @given(cmd=st.text(max_size=300))
    @PROPERTY_SETTINGS
    def test_deterministic_result(self, cmd: str):
        """Calling check_dangerous_command twice on the same input returns the same result.

        **Validates: Requirements 1.2**
        """
        result1 = check_dangerous_command(cmd)
        result2 = check_dangerous_command(cmd)
        assert result1 == result2

    @given(
        prefix=st.text(max_size=50),
        pattern_tuple=_dangerous_pattern,
        suffix=st.text(max_size=50),
    )
    @PROPERTY_SETTINGS
    def test_embedded_dangerous_pattern_detected(self, prefix: str, pattern_tuple: tuple, suffix: str):
        """Commands containing a substring that matches a dangerous pattern are detected.

        Constructs a command by embedding a concrete match for a dangerous regex
        pattern between random prefix/suffix text, then verifies check_dangerous_command
        returns a non-None reason string.

        **Validates: Requirements 1.2**
        """
        regex_pattern, expected_reason = pattern_tuple

        # Generate a concrete string that matches the regex pattern
        concrete = _concrete_match_for(regex_pattern)
        if concrete is None:
            # If we can't generate a concrete match, skip this example
            return

        cmd = prefix + concrete + suffix

        # Verify the regex actually matches our constructed command
        if not re.search(regex_pattern, cmd, re.IGNORECASE):
            return

        result = check_dangerous_command(cmd)
        assert result is not None, (
            f"Expected detection for pattern '{regex_pattern}' in command '{cmd}'"
        )

    @given(cmd=st.text(max_size=300))
    @PROPERTY_SETTINGS
    def test_result_is_reason_string_or_none(self, cmd: str):
        """check_dangerous_command returns either a known reason string or None.

        **Validates: Requirements 1.2**
        """
        result = check_dangerous_command(cmd)
        if result is not None:
            valid_reasons = {reason for _, reason in DANGEROUS_PATTERNS}
            assert result in valid_reasons, (
                f"Returned reason '{result}' is not in DANGEROUS_PATTERNS reasons"
            )


# ---------------------------------------------------------------------------
# Helper: map each regex pattern to a concrete string that matches it
# ---------------------------------------------------------------------------

_CONCRETE_MATCHES: dict[str, str] = {
    r'rm\s+(-[rfRf]+\s+)?/': "rm -rf /",
    r'rm\s+(-[rfRf]+\s+)?~': "rm -rf ~",
    r'rm\s+-[rfRf]+': "rm -rf",
    r'dd\s+if=/dev/(zero|random|urandom)': "dd if=/dev/zero",
    r'mkfs': "mkfs",
    r'>\s*/dev/(sda|hda|nvme|vda)': "> /dev/sda",
    r':()\{:\|:&\};:': ":(){:|:&};:",
    r'chmod\s+(-R\s+)?777\s+/': "chmod 777 /",
    r'chown\s+-R\s+.*\s+/': "chown -R user /",
    r'curl\s+.*\|\s*(bash|sh)': "curl http://x | bash",
    r'wget\s+.*\|\s*(bash|sh)': "wget http://x | bash",
    r'sudo\s+rm': "sudo rm",
    r'>\s*/etc/': "> /etc/",
}


def _concrete_match_for(regex_pattern: str) -> str | None:
    """Return a concrete string known to match the given regex pattern, or None."""
    return _CONCRETE_MATCHES.get(regex_pattern)
