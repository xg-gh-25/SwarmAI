# Self-Evolution Engine — Supplementary Reference

This file provides additional detail beyond SKILL.md. SKILL.md is self-contained
and sufficient for all operations. Read this only when you need deeper context
on VFM scoring, SSE event fields, or entry revision operations.

## VFM Scoring Detail

Score across four dimensions (each 0-10):

| Dimension | Weight | Question |
|-----------|--------|----------|
| Reusability | 3x | Will future tasks leverage this repeatedly? |
| Error Prevention | 3x | Does this prevent a recurring failure? |
| Analysis Quality | 2x | Does this improve output depth or accuracy? |
| Efficiency Gain | 2x | Does this save time or reduce tool calls? |

**Formula:** `VFM = (Reusability*3 + ErrorPrevention*3 + AnalysisQuality*2 + EfficiencyGain*2) / 10`

| VFM Score | Action |
|-----------|--------|
| >=70 | Promote immediately |
| 50-69 | Promote if 3+ occurrences confirm the pattern |
| <50 | Keep in EVOLUTION.md, do not promote |

## SSE Event Field Details

### evolution_start
```json
{
  "type": "evolution_start",
  "data": {
    "triggerType": "reactive|proactive|stuck",
    "description": "What triggered it",
    "strategySelected": "compose_existing|build_new|...",
    "attemptNumber": 1,
    "principleApplied": "Reuse before you build"
  }
}
```

### evolution_result
```json
{
  "type": "evolution_result",
  "data": {
    "outcome": "success|failure",
    "durationMs": 5000,
    "capabilityCreated": "E006",
    "evolutionId": "E006",
    "failureReason": null
  }
}
```

### evolution_stuck_detected
```json
{
  "type": "evolution_stuck_detected",
  "data": {
    "detectedSignals": ["repeated_error", "cosmetic_retry"],
    "triedSummary": "Tried X and Y, both failed",
    "escapeStrategy": "completely_different"
  }
}
```

### evolution_help_request
```json
{
  "type": "evolution_help_request",
  "data": {
    "taskSummary": "What was being attempted",
    "triggerType": "reactive",
    "attempts": [
      {"strategy": "compose_existing", "failureReason": "No matching capability"},
      {"strategy": "build_new", "failureReason": "Script failed verification"},
      {"strategy": "research_and_build", "failureReason": "No relevant docs found"}
    ],
    "suggestedNextStep": "Install X manually or provide API key"
  }
}
```

## Entry Revision Operations

Beyond basic add, entries can be revised:

- **`supersede`**: Replace an entry with a better one. Set `Status: superseded by E{NNN}`.
- **`fork`**: Create a variant for a different context. Reference parent.
- **`contest`**: Mark conflicting evidence against an entry. Add note.

Log all revisions to JSONL changelog.
