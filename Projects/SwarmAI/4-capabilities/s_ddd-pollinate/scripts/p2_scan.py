#!/usr/bin/env python3
"""P2 Hero Framing Scanner — Mechanical gate for Pollinate content.

Scans text for first-person hero framing patterns. Returns exit 0 if clean,
exit 1 if violations found (prints offending lines to stdout).

Usage:
    echo "content" | python p2_scan.py
    python p2_scan.py < file.html
    python p2_scan.py path/to/file.txt

Design: Targets specific HERO framing patterns (achievement claims, superiority
assertions) — NOT all first-person usage. "我们的设计哲学" as a section header
discussing philosophy is legitimate. "我造了一个系统" as a claim is not.

Hero framing = claiming credit for building/creating/achieving.
Design discussion = talking ABOUT principles without claiming personal achievement.
"""

import re
import sys
from pathlib import Path

# Hero framing patterns — achievement claims and superiority assertions
# These are the specific phrases that indicate P2 violation (hero framing)
# NOTE: \b word boundaries do NOT work for CJK — never add them to ZH patterns.
HERO_PATTERNS_ZH = [
    r"我造了",          # I created/built
    r"我做了",          # I made/did
    r"我搞了",          # I did/made (colloquial)
    r"我弄了",          # I made (colloquial)
    r"我写了",          # I wrote
    r"我研发了",        # I R&D'd
    r"我搭建了",        # I set up/built
    r"我完成了",        # I completed/achieved
    r"我发明了",        # I invented
    r"我们是.{0,10}(最|领先|前沿|顶尖|一流)",  # We are the most/leading/cutting-edge
    r"我们的.{0,15}(远超|碾压|吊打|秒杀|领先)",  # Our X far exceeds / crushes
    r"我(们)?打造",     # I/We forged/crafted
    r"我(们)?构建了",   # I/We constructed
    r"我(们)?开发了",   # I/We developed
    r"我(们)?实现了",   # I/We achieved/implemented (as claim)
    r"我(们)?创造了",   # I/We created
]

HERO_PATTERNS_EN = [
    # Catch-all with contractions and tense variants
    r"\b(I|We)('ve| have) (built|created|developed|designed|engineered|architected)\b",
    r"\b(I|We) (built|created|developed|designed|engineered|architected|launched|shipped|delivered|pioneered|invented)\b",
]

# Allowlist patterns — legitimate uses that should NOT trigger
# Section headers discussing philosophy are OK
ALLOWLIST_PATTERNS = [
    r"^##\s+我们的",     # Markdown section header starting with "我们的"
    r"^#\s+我们的",      # H1 header
]


def strip_html(text: str) -> str:
    """Strip HTML tags and collapse whitespace if input looks like HTML."""
    lower = text.lower()
    if "<html" in lower or "<!doctype" in lower or "<div" in lower:
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
        text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL)
        text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", "\n", text)
    return text


def scan_text(text: str) -> list[tuple[int, str, str]]:
    """Scan text for P2 hero framing violations.

    Returns list of (line_number, line_content, matched_pattern) tuples.
    Empty list = clean.
    """
    text = strip_html(text)
    violations = []
    lines = text.split("\n")

    for line_num, line in enumerate(lines, 1):
        # Skip allowlisted patterns
        if any(re.search(pat, line) for pat in ALLOWLIST_PATTERNS):
            continue

        # Check hero patterns
        for pattern in HERO_PATTERNS_ZH + HERO_PATTERNS_EN:
            if re.search(pattern, line, re.IGNORECASE):
                violations.append((line_num, line.strip(), pattern))
                break  # One violation per line is enough

    return violations


def main() -> int:
    """Main entry point. Reads from stdin or file arg. Returns exit code."""
    # Read input
    if len(sys.argv) > 1:
        filepath = Path(sys.argv[1])
        if not filepath.exists():
            print(f"Error: file not found: {filepath}", file=sys.stderr)
            return 2
        text = filepath.read_text(encoding="utf-8")
    else:
        text = sys.stdin.read()

    violations = scan_text(text)

    if not violations:
        return 0

    # Report violations
    print(f"P2 VIOLATION: {len(violations)} hero framing instance(s) found:")
    for line_num, line, pattern in violations:
        print(f"  L{line_num}: {line}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
