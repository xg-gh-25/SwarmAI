---
name: Token Estimator
description: >
  Estimate token count for files using the bundled script.
  TRIGGER: "estimate tokens", "count tokens", "token usage", "context window size".
  DO NOT USE: for manual token math — always run the script.
version: 1.0.0
tags: [skill, estimate-tokens, tokens, word-count, ai, context-window]
---

# Estimate Tokens

## Overview

**Agents use the bundled script to estimate token counts for files.** Never perform manual calculations - always run the script for accurate, formatted results.

## Usage

When users need to:
- Estimate token count for files before AI processing
- Check if files fit within context windows
- Analyze file size for token budgeting
- Understand context window utilization

**Concrete triggers:**
- "estimate tokens for this file"
- "how many tokens is this?"
- "will this fit in context?"
- "count tokens in file"

## Instructions

You **MUST** use the bundled script for all token estimation requests. You **MUST NOT** perform manual calculations or math.

### Token Estimation Process

1. Run the script with the file path:
   ```bash
   ./scripts/estimate-tokens.sh <filepath>
   ```

2. **Preferred**: For command output, pipe directly from the source command:
   ```bash
   ls ~/.swarm-ai/skills/ | ./scripts/estimate-tokens.sh
   command | ./scripts/estimate-tokens.sh
   ```

3. Alternative: Use echo only when direct piping isn't feasible:
   ```bash
   echo "content" | ./scripts/estimate-tokens.sh
   ```

4. Present the script output directly to the user

### Best Practices

- **Always prefer piping from source commands** over echo when possible
- Run the actual command that generates the output rather than recreating it
- Use echo only as a fallback when the source command cannot be executed

## Deterministic Scripts

### scripts/estimate-tokens.sh
Calculates token estimates with formatted output.

**When to use:** For all token estimation requests

**Parameters:**
- `<filepath>` - Path to file for token estimation
- Accepts piped input from stdin when no filepath provided

**What it does:**
- Counts words using `wc -w`
- Multiplies by 1.8 (average tokens per word)
- Calculates percentage of 200k context window
- Displays formatted results

**Output format:**
```
File: filename.txt
Words: 1,234
Estimated tokens: 2,221
Context usage: 1.11% of 200,000 tokens
```

## Core Concepts

### Token Estimation
- **Conversion ratio**: 1.8 tokens per word (average for English text)
- **Context window**: 200,000 tokens used as standard reference
- **Accuracy**: Approximation suitable for planning, not exact counting

### Agent Behavior
- You **MUST** run the script for all token estimation requests
- You **MUST NOT** perform manual calculations
- You **MUST** present script output directly to users

## Quick Reference

| Operation | Command |
|-----------|---------|
| Estimate tokens | `./scripts/estimate-tokens.sh file.txt` |
| Piped input | `command \| ./scripts/estimate-tokens.sh` |

## Common Mistakes

### Using Echo Instead of Source Commands
**Problem:** Using `echo` to recreate command output instead of running the source command
**Preferred:** `ls ~/.swarm-ai/skills/ | ./scripts/estimate-tokens.sh`
**Acceptable:** `echo "content" | ./scripts/estimate-tokens.sh` (when source unavailable)

### Manual Calculation
**Problem:** Agent performs math instead of using script
**Fix:** Always run `./scripts/estimate-tokens.sh` - never calculate manually

### Guessing Token Counts
**Problem:** Providing estimates without running the script
**Fix:** Execute the script for accurate, formatted results
