# INSTRUCTIONS ref — Progress Display (3 phases, 10 landmarks, 3 gates)
Read when: emitting a phase header, a ★ gate verdict, or the ① EVALUATE requirement block, or when
output is under token pressure. The per-stage landmark line formats (①–⑩) are on the card.

**Principle:** the pipeline execution IS the live demo. A user reading the chat should understand
which phase, which stage, what Gate 0 / Gate 1 / Gate 2 decided, and what each stage produced —
without reading INSTRUCTIONS.md. **10 landmarks = 9 stages + Gate-1's ④ PRE-CHECK** (a gate, not a
stage; the 9-stage SSOT is `pipeline_profiles.py`). Gate 0 guards the *framing* (inside EVALUATE),
Gate 1 the *plan* (after PLAN), Gate 2 the *code* (inside DELIVER). Gate 0 and Gate 2 ride within a
stage (no own circled number); Gate 1 is the ④ landmark.

```
PHASE A: DECISION    ①②③④     — shared by both modes
PHASE B: EXECUTION   ⑤⑥⑦      — full (one-shot) or goal (iterative)
PHASE C: DELIVERY    ⑧⑨⑩     — shared quality gate + knowledge loop
```
**Phase headers** — once when entering a new phase (full/bugfix only):
```
---
**PHASE A: DECISION**
---
```
**Stage landmarks** — after each stage completes:
```
## ✦ <circled_number> STAGE_NAME
→ <key result in one line>
  <optional: DDD insight or Gate verdict>
```
**Gate verdicts** — visually prominent when a gate fires:
```
## ★ GATE 0: UNDERSTANDING (diagnose-before-build, inside EVALUATE)
→ PASS | work_type: greenfield | understanding SUPPORTED (skeptic), ambiguity 0 hits, WB block ✓
  (or) → BLOCK | Understanding gate: claim is a plan, not present-state (M1)
         Re-frame: describe what IS, move the fix to THINK

## ★ GATE 1: SKEPTIC + SSA
→ PASS | all 5 checks clean
  (or) → BLOCK | Check 4 SSA: plan targets symptom, not root cause
         Structural alternative: fix producer to guarantee dict shape
         → revising the plan (retry 1/2)

## ★ GATE 2: ADVERSARIAL
→ 3 findings (1 HIGH, 2 LOW) | 3 fixed, 0 remaining
  Convergence: 6/6 layers pass (1 iteration)
```

## Real-time requirement emit (Phase-2; after ① EVALUATE, when it produced a structured `requirements[]`)
The user should see "what the AI understood + where it decided for me" right after the ① EVALUATE
landmark, not buried in the run-end REPORT (design §4.6.4). **Do NOT hand-type it.** The EVALUATE
publish returns the block CLI-rendered as the `_display_block` field of its (non-`--quiet`) result
JSON — relay that into the ① landmark. Its format is the REPORT §4.6 provenance-audit markdown
(`## 需求溯源审计`: FR derivation table + 👤/🧠 clarification Q&A + ⚠️ leftover), rendered from the
same `requirements[]`/`clarifications[]` as the archive so the two cannot drift. Illustrative sketch:
```
📋 需求理解(EVALUATE 产出 · 实时)
  FR-1 [👤 user-stated]   <statement>          ✅ confirmed
  FR-2 [🧠 self-answered] <statement>(依据…)   ⚠️ assumed    ← 我替你拍的板,看一眼
  ❓ 待你确认(若有 clarification-escalate):Q1 …(A/B/X.Other)
  ⚠️ 承重假设:FR-2 若错则 BUILD 返工 → 建议现在确认
```
<!-- R-instr-014 -->
Rules: answerer-distinguished (`👤 user` vs `🧠 self-answered` are visually distinct — the
self-answered rows are what the user most needs to audit); load-bearing assumptions carry the
`← 建议现在确认` flag. The `_display_block` is returned ONLY on a non-`--quiet` publish
(desktop/direct); a background/headless run publishes with `--quiet` → no block, and the §4.6 audit
surfaces at the run-end REPORT + Pipeline card instead (never lost, a different surface). Skip the
inline emit only when EVALUATE produced no structured `requirements[]` — a RELAXED profile
(trivial/docs/research) or a legacy old-shape run; the free-text landmark is then enough. ⚠️ On a
STRICT profile (full/bugfix/goal) EVALUATE is required to emit the structured block (evaluate.md
Structured Requirements `[MUST]`), so "no requirements[]" is NOT a legitimate skip there —
produce it.

## Token Conservation Rules (rule 1, the `next_action` exemption, is on the card)
2. **Tool output: compact only** — tests through `pipeline_test.py` (one compact JSON; never pipe
   pytest to tail/head), git `--stat`.
3. **No prose between stages** — the landmark IS the output.
4. **Validator: suppress when valid** — show only on failure.
5. **DDD reads: batch at EVALUATE** — don't re-read per stage.
6. **Commit messages: HEREDOC, no preview.**
7. **NO text before tool calls (AGENT R20 "Tool calls are silent").** No line of plan before a tool
   call ("下一步我要做 X"); that belongs in reasoning. Visible per stage: the phase header, the
   landmark, a gate verdict, and a decision that genuinely needs the user. Silence between tool
   calls is correct (pre-call text is also where a corrupted tool-call opener lands and is then
   copied from history).

Token targets: full <14K (phase headers + full landmarks + gate verdicts) · bugfix <8K (compact
landmarks + gate verdicts) · trivial <6K (compact landmarks, no phase headers) · research <4K.
Emergency compress: `A: ①GO ②3alt ③4AC ④★PASS | B: ⑤3R3G ⑥clean ⑦28/0 | C: ⑧★2fix ⑨6L-pass ⑩2lessons`

**Display rules:** circled numbers MANDATORY on every stage landmark; phase dividers at each phase
transition (full/bugfix only); gates use `★`, stages `✦`; DDD insights only when real (silence >
false attribution); density is flexible (trivial compresses to one line per stage).
