# BUILD ref — Gate 1 Pre-Check (Skeptic + SSA)

Read when the profile runs Gate 1 (table below), or when a Gate 1 verdict, a rejected
spawn, or the Gate 1 record needs handling.

## Gate 1: Pre-Check (Skeptic + SSA) — ACTIVE `[MUST]` <!-- R-build-003 -->

Status ACTIVE: a BLOCK verdict halts BUILD until resolved. The user can override with
"proceed anyway"; that is logged as an override, never a silent skip.

> **Enforcement level `[MUST]` (behavioral, not code-gated).** The Skeptic+SSA sub-agent
> spawn is agent discipline: the validator records `gate1_verdict` in the run/REPORT but
> does NOT BLOCK on it (`grep gate1_verdict pipeline_validator.py` = 0 hits; only
> `artifact_cli.py` REPORT rendering reads it). Same class as evaluate.md's M3 skeptic.
> A blocked spawn is recoverable at Gate 2 (which IS code-enforced), so `[MUST]` here is
> by design; see the `gate_spawn_blocked` fail-closed rule below. <!-- R-build-004 -->

## Trigger conditions

| Profile | Gate 1? | Rationale |
|---------|:---:|-----------|
| full | ✅ Always | Maximum rigor: a wrong direction is multiplied by the full BUILD |
| goal | ✅ First cycle only | Fires once before goal_cycle begins, not per cycle |
| bugfix | ✅ Always | Highest patch risk; SSA is most valuable here |
| trivial | ❌ Skip | Single config/const change, no direction to validate |
| research | ❌ Skip | No code generation |
| docs | ❌ Skip | No code generation |

If skipped, record `"gate1_verdict": "SKIPPED", "gate1_reason": "profile=trivial",
"gate1_blocking": false` (reason names the actual profile).

## Inputs (feed all 6 to the sub-agent; any may be empty)

`stage-start --stage build` (and `goal_cycle`) writes them into the prompt for you:
`gate1_prompt_path` is the gate prompt with the six inputs below already filled, so the
skeptic does not spend tool calls re-reading them. Assemble by hand only when stage-start
reports a warning instead of the path.

1. **Plan artifact**: the PLAN design_doc (stage-start returns it as upstream).
2. **Rejected alternatives**: THINK's "alternatives considered and why rejected", so the
   Skeptic does not re-challenge dismissed options. THINK explored none → omit (the
   sub-agent skips deconfliction).
3. **TECH.md `## Blocking Constraints`**, if the section exists. Missing → omit (Check 1
   passes automatically).
4. **IMPROVEMENT.md "What Failed"** `[pitfall]` entries, for failure-pattern matching.
   None → omit (Check 2 passes automatically).
5. **EVALUATE anti-repetition verdict**: if EVALUATE's Anti-Repetition Check matched but
   passed with reasoning ("PROCEED — structural difference: X"), include it so Gate 1
   does not re-litigate what EVALUATE resolved.
6. **`understanding.work_type`** from the EVALUATE artifact (`bugfix | existing-feature |
   greenfield | refactor | research | docs`). **REQUIRED: Check 4 (SSA) polarity depends
   on it.** The sub-agent has zero session context and cannot infer it, so feed it
   verbatim (as evaluate.md's M3 skeptic is fed `Work type: <work_type>`). If absent the
   sub-agent defaults to the bugfix/patch-tolerant polarity, so a refactor whose
   work_type is not fed silently loses its inverted-polarity protection.

## Prompt

The prompt lives in `data/gate-prompts/gate1-skeptic.md`; never retype it.
`stage-start --stage build` writes it with its inputs (and a digest of the whole plan) to
`gate1_prompt_path` and records the hand-out (`run.json gate1_handout`). On a BUILD
re-entry it returns `gate1_verdict` (+ `gate1_override`) instead of a path only when the
prompt is byte-identical to one it handed out AND the latest build record's Gate-1 fields
changed after that hand-out (a verdict was recorded for exactly that prompt): do not
spawn Gate-1 again. A BLOCK without an override is always handed out again. Only
without either key, emit it by hand from the repo root:

`python backend/skills/s_autonomous-pipeline/scripts/spawn_prompt.py --gate gate1-skeptic`

Use the output ONLY on exit 0. Exit 2 means the prompt states no numeric cap and stdout
is deliberately EMPTY; pasting it blindly would spawn with no prompt at all.

## Execution

1. Assemble the 6 inputs above.
2. Spawn a fresh-context sub-agent with the emitted Skeptic+SSA prompt as system prompt
   and the inputs as the user message, with `run_in_background: false` (a background
   spawn ends the visible stream).
3. Parse the verdict from the sub-agent response, then handle it (next section).

**Spawn rejected? Fail closed (`gate_spawn_blocked`).** If the harness REJECTS the
step-2 spawn ("user doesn't want to proceed with this tool use" / "does not want to take
this action"), apply the spawn-rejection rule in INSTRUCTIONS (Rules, `gate_spawn_blocked`):
retry the spawn EXACTLY ONCE → still rejected → **CHECKPOINT, reason="gate_spawn_blocked"**,
do NOT proceed to BUILD. Resume re-enters on a fresh subprocess, which clears the
poisoning. NEVER self-review the plan in place of the Skeptic+SSA spawn: an unverified
BUILD direction is exactly what Gate 1 exists to prevent. Unlike Gate 2, Gate 1 has no
code-enforced evidence field; this instruction is the guard, and a blocked Gate 1 is
recoverable at Gate 2, so instruction-only is sufficient here.

## Verdicts

**PASS** → show `✅ GATE 1: PASS — direction validated, proceed to BUILD.` and continue.

**WARN** → show `⚠️ GATE 1: WARN` + one line per non-PASS check + `→ Acknowledged.
Proceeding to BUILD with noted concerns.` Log the warnings and continue; no user interrupt.

**BLOCK** → present to the user:

```
🛑 GATE 1: BLOCK — direction challenge
  Issue: {specific finding from sub-agent}
  Structural alternative: {what the sub-agent proposed}
  [Fix Plan — revise and re-run Gate 1]  [Proceed Anyway — accept risk]
```

- **Fix Plan:** return to PLAN, revise the approach, re-run Gate 1. Max 2 retries.
- **Proceed Anyway:** explicit user override. Log it (`gate1_override: true`) and continue.
  The override is NOT silent: it is recorded in run.json for signal-quality tracking.

## Recording

Write the verdict as soon as Gate 1 completes (it survives a mid-BUILD checkpoint):

`python backend/scripts/artifact_cli.py run-update --project <P> --run-id <R> --stage-json '{"stage":"build","gate1_verdict":"PASS","gate1_blocking":true,"gate1_override":false,"gate1_retries":0,"gate1_checks":{"constraints":"PASS","failure_pattern":"PASS","simplicity":"WARN: ...","ssa":"N/A","api_existence":"PASS"}}'`

Then repeat the same `gate1_*` keys in the `record` of the build `stage-complete` data.
`stage-complete` REPLACES the build stage record and carries forward only `artifact_id`,
`adversarial_review` and `lessons`, so a verdict written only by `run-update` is lost.

Fields:
- `gate1_verdict`: PASS, WARN, BLOCK or SKIPPED
- `gate1_blocking`: always `true` (the gate is active); `false` only with SKIPPED
- `gate1_override`: `true` if the user chose "Proceed Anyway" on a BLOCK
- `gate1_checks`: per-check detail for signal-quality tracking
- `gate1_retries`: PLAN revisions attempted (0, 1 or 2)

Override tracking: with `gate1_override: true`, OS Eval can later judge whether the
override was justified (the plan succeeded despite BLOCK) or the BLOCK was right (the
plan produced rework). That data tunes the prompt.
