#!/bin/bash
# check-weasel-words.sh - Find weasel words in document
# Usage: ./check-weasel-words.sh <file>

set -euo pipefail

if [ $# -eq 0 ]; then
    echo "Usage: $0 <file>" >&2
    exit 1
fi

FILE="$1"

if [ ! -f "$FILE" ]; then
    echo "Error: File not found: $FILE" >&2
    exit 1
fi

# Weasel words pattern
PATTERN="generally|usually|might|could|should|approximately|roughly|about|around|nearly|almost|fairly|quite|rather|somewhat|relatively|significantly|substantially|considerably|mostly|largely|primarily|mainly|typically|normally|commonly|frequently|often|rarely|seldom|occasionally|sometimes|perhaps|possibly|probably|likely|unlikely|may|can|would|seem|appear|tend"

echo "Checking for weasel words in: $FILE"
echo "----------------------------------------"

if grep -inE "\b($PATTERN)\b" "$FILE"; then
    echo "----------------------------------------"
    echo "Found weasel words. Replace with specific metrics and commitments."
    exit 1
else
    echo "No weasel words found."
    exit 0
fi
