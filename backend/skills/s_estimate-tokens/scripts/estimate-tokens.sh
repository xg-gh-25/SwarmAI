#!/bin/bash
set -euo pipefail

# Estimate tokens for files using word count * 1.8
# Usage: ./estimate-tokens.sh <filepath> [filepath2] ...

if [ $# -eq 0 ]; then
    echo "Usage: $0 <filepath> [filepath2] ..."
    echo "Estimates token count using word count * 1.8"
    exit 1
fi

for filepath in "$@"; do
    if [ ! -f "$filepath" ]; then
        echo "Error: File '$filepath' not found" >&2
        continue
    fi
    
    # Get word count
    words=$(wc -w < "$filepath")
    
    # Calculate estimated tokens (words * 1.8)
    tokens=$(echo "$words * 1.8" | bc -l | cut -d. -f1)
    
    # Calculate percentage of 200k context window
    percentage=$(echo "scale=2; $tokens / 200000 * 100" | bc -l)
    
    # Format numbers with commas
    words_formatted=$(printf "%'d" "$words")
    tokens_formatted=$(printf "%'d" "$tokens")
    
    echo "File: $(basename "$filepath")"
    echo "Words: $words_formatted"
    echo "Estimated tokens: $tokens_formatted"
    echo "Context usage: ${percentage}% of 200,000 tokens"
    echo
done
