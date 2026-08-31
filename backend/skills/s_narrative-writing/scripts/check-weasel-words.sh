#!/bin/bash
# check-weasel-words.sh - Find weasel words in a document
# Usage: ./check-weasel-words.sh <file>
#
# Two word classes (see reference/weasel-words.md for the authoritative list):
#   COMMITTED — words that are weasel in any casing (vague qualifiers, uncertain
#     verbs, vague descriptors, unquantified quantities). Matched CASE-INSENSITIVE.
#   MODAL — should/may/can/would/might/could and the uncertain verbs. Weasel ONLY
#     as lowercase hedging ("we should improve"). NOT weasel as an RFC-2119 keyword,
#     which by convention is ALL-CAPS and usually markdown-bold (**MUST**/**SHOULD**).
#     Matched LOWERCASE-ONLY, and markdown-bold RFC-2119 keywords plus fenced/inline
#     code are excluded, so instructional MUST/SHOULD prose is not false-flagged.
#
# Known limitation: a sentence-initial capitalized modal ("Should we ship?") is not
# flagged, since capitalization cannot be distinguished from RFC-2119 there. Rare in
# narrative body; catch it by eye.
#
# Exit code: 1 if weasel words found, 0 if clean.

set -uo pipefail

if [ $# -eq 0 ]; then
    echo "Usage: $0 <file>" >&2
    exit 1
fi

FILE="$1"

if [ ! -f "$FILE" ]; then
    echo "Error: File not found: $FILE" >&2
    exit 1
fi

# COMMITTED weasels — flagged in any casing.
COMMITTED="generally|usually|approximately|roughly|about|around|nearly|almost|fairly|quite|rather|somewhat|relatively|significantly|substantially|considerably|mostly|largely|primarily|mainly|typically|normally|commonly|frequently|often|rarely|seldom|occasionally|sometimes|perhaps|possibly|probably|soon|seamless|seamlessly|robust|leverage|synergy|synergies|various|several|many|most|few|much|very|really|numerous|multiple"

# MODAL/hedge words — weasel only as LOWERCASE hedging, not as ALL-CAPS RFC-2119.
MODAL="should|may|can|would|might|could|likely|unlikely|seem|seems|appear|appears|tend|tends"

# Strip fenced code blocks (```...```) and inline code (`...`) before scanning —
# a modal inside `git commit` or a code sample is not prose hedging.
# Strip inline `code` and fenced ```blocks``` before scanning. An unterminated
# fence is a malformed doc — warn and DO NOT drop the tail (fail-loud, not
# fail-open: a lint gate must never silently skip half the document).
STRIPPED="$(awk '
  /^[[:space:]]*```/ { infence = !infence; next }
  infence { next }
  { gsub(/`[^`]*`/, ""); print }
  END { if (infence) print "check-weasel-words: WARNING unterminated code fence — scanned all lines anyway" > "/dev/stderr" }
' "$FILE")"
# If the awk warned about an unterminated fence, re-scan WITHOUT fence-stripping
# so no prose is skipped (only inline-code stripped).
if awk '/^[[:space:]]*```/{n++} END{exit !(n%2)}' "$FILE"; then
    STRIPPED="$(awk '{ gsub(/`[^`]*`/, ""); print }' "$FILE")"
fi

echo "Checking for weasel words in: $FILE"
echo "----------------------------------------"

found=0

# COMMITTED — case-insensitive. MODAL — lowercase-only, with bold RFC-2119
# keywords stripped PER-MATCH (sed, not grep -v) so a line that has BOTH a
# **SHOULD** keyword AND real lowercase hedging still reports the hedging.
committed_hits="$(printf '%s\n' "$STRIPPED" | grep -inE "\b($COMMITTED)\b" || true)"
modal_hits="$(printf '%s\n' "$STRIPPED" \
    | sed -E 's/\*\*(MUST|SHOULD|MAY|CAN|WOULD|MIGHT|COULD|SHALL)\*\*//g' \
    | grep -nE "\b($MODAL)\b" \
    || true)"

# Merge both streams, dedup, and sort by line number so hits read in document order.
all_hits="$(printf '%s\n%s\n' "$committed_hits" "$modal_hits" \
    | sed '/^$/d' | sort -t: -k1,1n -u || true)"
if [ -n "$all_hits" ]; then
    printf '%s\n' "$all_hits"
    found=1
fi

echo "----------------------------------------"
if [ "$found" = 1 ]; then
    echo "Found weasel words. Replace with specific metrics and commitments."
    echo "(COMMITTED weasels flag in any casing; MODAL words flag only as lowercase"
    echo " hedging — ALL-CAPS RFC-2119 keywords and code are intentionally exempt.)"
    exit 1
else
    echo "No weasel words found."
    exit 0
fi
