# Humanize Skill Testing

Evaluation scenarios and validation commands for testing the humanize skill.

## Model Coverage

| Model Class | Tested | Notes |
|-------------|--------|-------|
| Large (e.g., Opus) | Yes | Primary development target. Best at preserving word count. |
| Medium (e.g., Sonnet) | Yes | Good performance. Occasionally over-summarizes; remind about ±20 rule. |
| Small (e.g., Haiku) | Partial | Can handle simple documents. May miss subtle patterns. |

## Evaluation Scenarios

### Scenario 1: Basic Pattern Removal

**Input:** Academic paragraph with 3+ "By [gerund]" patterns
**Expected Behavior:**
- All "By [gerund]" patterns replaced with varied alternatives
- Word count within ±20 of original
- No new em-dashes added
- Technical terms preserved

**Validation:**
```bash
# Check word count
wc -w before.txt after.txt

# Check "By [gerund]" removed
grep -oiE '\bBy [a-z]+ing\b' after.txt | wc -l  # Should be 0 or reduced

# Check no new em-dashes
grep -o '—' before.txt | wc -l
grep -o '—' after.txt | wc -l  # Should be ≤ before
```

**Failure Indicators:**
- Word count changed by >20 words
- "By [gerund]" patterns still present
- New em-dashes introduced

---

### Scenario 2: Em-Dash Elimination

**Input:** Blog post with 5+ em-dashes
**Expected Behavior:**
- All em-dashes replaced with periods, commas, colons, or parentheses
- Sentence flow maintained
- Word count stable

**Validation:**
```bash
grep -o '—' after.txt | wc -l  # Should be 0
```

**Failure Indicators:**
- Em-dashes remaining
- Awkward sentence breaks where em-dashes were

---

### Scenario 3: Burstiness Injection

**Input:** Document with uniform 12-18 word sentences
**Expected Behavior:**
- 20-30% sentences become very short (<6 words)
- 20-30% sentences become very long (>25 words)
- Content preserved, only structure changed

**Validation:**
```bash
# Check sentence length distribution
cat after.txt | tr '.!?' '\n' | awk 'NF>0 {print NF}' | sort -n | uniq -c
# Look for variety: some <6, some >25
```

**Failure Indicators:**
- All sentences still uniform length
- Content changed to achieve burstiness

---

### Scenario 4: Word Count Preservation

**Input:** 500-word technical document
**Expected Behavior:**
- Final word count: 480-520 words
- All edits restructure rather than condense

**Validation:**
```bash
wc -w before.txt after.txt
# Difference must be ≤20
```

**Failure Indicators:**
- Word count dropped significantly (summarization occurred)
- Word count increased significantly (padding occurred)

---

### Scenario 5: Content Integrity

**Input:** Research summary with specific claims
**Expected Behavior:**
- No new anecdotes or experiences added
- No statistics or citations fabricated
- All original claims preserved
- Hedging doesn't reverse author's confidence

**Validation:**
Manual review: Compare each paragraph. Ask "Does the edited version claim anything not in the original?"

**Failure Indicators:**
- New "I've found that..." or "In my experience..." phrases
- New statistics or percentages
- Opinions added that weren't in source

---

### Scenario 6: Edge Case - Short Document

**Input:** 150-word abstract
**Expected Behavior:**
- Only 3-5 edits applied
- Most critical patterns fixed first
- Burstiness targets may not be fully met (acceptable)

**Failure Indicators:**
- Over-editing relative to document length
- Document feels completely rewritten

---

### Scenario 7: Edge Case - Already Human Text

**Input:** Text that scores <70% AI on detectors
**Expected Behavior:**
- Minimal changes (1-3 edits maximum)
- Focus only on obvious patterns
- Original voice preserved

**Failure Indicators:**
- Extensive edits to already-good text
- Introducing patterns that weren't there

## Validation Commands Reference

```bash
# Word count
wc -w FILE

# Em-dash count
grep -o '—' FILE | wc -l

# Question mark count
grep -o '?' FILE | wc -l

# Connector frequency
grep -oiE '\b(moreover|furthermore|additionally|however|consequently|nevertheless|therefore)\b' FILE | sort | uniq -c | sort -rn

# Sentence length distribution
cat FILE | tr '.!?' '\n' | awk 'NF>0 {print NF}' | sort -n | uniq -c

# "By [gerund]" count
grep -oiE '\bBy [a-z]+ing\b' FILE | wc -l

# "This [verb] that" count
grep -oiE '\bThis [a-z]+ that\b' FILE | wc -l
```

## Known Limitations

1. **Very short documents** (<100 words): Full burstiness targets may not be achievable
2. **Highly technical content**: Vocabulary substitution limited; focus on structure
3. **Poetry/creative writing**: Not designed for this; humanization may harm intentional style
4. **Non-English text**: Not tested; patterns may differ by language
