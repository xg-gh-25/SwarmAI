# THINK ref — Self-Socratic ambiguity re-scan

Read when: the re-scan (card step 5) finds a hit, or you need the term list and field shape.
The gate itself (`_check_ambiguity_scan`) is on the card.

`INTERROGATE YOUR OWN ASSUMPTIONS — NOT THE USER`

After the Design Risk Probe has produced its `verification` text and you have chosen a
recommendation, re-scan the probe assumptions + recommendation for residual ambiguity. One
self-answer round. This is the THINK-side mirror of EVALUATE's ambiguity scan (see
`stages/evaluate.md` and its refs for the philosophy: interrogate the framing, not the user;
the Understanding Gate's "refute your claim" discipline applied at the design layer).

## Process (ONE round)

1. Scan the `risk_probe[].verification` strings and the `recommendation` for ambiguity/hedge
   terms: `depends`, `maybe`, `not sure`, `mix of`, `somewhere between`, `standard`, `typical`
   + CJK `看情况 / 可能 / 大概 / 差不多 / 视情况 / 标准做法 / 一般`
   (canonical: `pipeline_validator._AMBIGUITY_TERMS`).
2. For each hit: self-answer by reading code/DDD (e.g. a "standard pattern" must name the EXACT
   pattern + file). Escalate only genuinely user-intent ambiguity.
3. Record the scan in the research artifact's `ambiguity_scan` field (same shape as EVALUATE).

## Field shape

```json
{"ambiguity_scan": {
  "scanned_fields": ["recommendation", "risk_probe.verification"],
  "terms_checked": ["depends", "maybe", "not sure", "mix of", "somewhere between",
                    "standard", "typical", "看情况", "可能", "大概", "差不多", "视情况",
                    "标准做法", "一般"],
  "hits": [{"term": "standard", "where": "recommendation",
            "resolution": "means the BackgroundHookExecutor enqueue pattern in lifecycle_manager.py",
            "kind": "self-answer"}],
  "hit_count": 1,
  "all_resolved": true}}
```

`kind` is `self-answer` or `escalation`. Each `resolution` must be non-empty and at least
12 characters or the validator BLOCKS (strict profiles). `hits: []` with `hit_count: 0` is
valid: scanned, clean.
