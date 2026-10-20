# EVALUATE ref — Structured Requirements (`requirements[]`), clarifications, real-time preview
Read when: filling `requirements[]` / `clarifications[]`, recording an MCQ answer, or relaying
the requirements preview. The card (`stages/evaluate.md` § Structured Requirements) holds the
`[MUST]` itself.
## Why it is required production (design §4.1 / §4.4 / §4.6.4)
A strict-profile EVALUATE (full/bugfix/goal) MUST emit a structured `requirements[]` array AND
`schema_version:1` AND `clarifications[]`. It is still WARN-only at the validator layer (no
publish BLOCK, per the design's strangler-fig); "MUST" is agent discipline, the same class as
the M3 skeptic. Do NOT read "no BLOCK" as "skippable": the three downstream consumers (the
REPORT §4.6 audit block, the `validate_requirements_block` WARN layer, and the §4.6.4
real-time inline emit) ALL read THIS artifact's `requirements[]`, and light up only when you
emit it. A strict EVALUATE that ships only free-text `acceptance_criteria` leaves them dark.
Autonomy-first: this is a RECORD of what you already decided; it adds NO default prompting.
**`requirements[]` and `schema_version:1` are a NON-OPTIONAL PAIR: emit BOTH or neither.**
`validate_requirements_block` SILENTLY returns `[]` (zero WARN) when `schema_version` is absent
or `< 1`, and the REPORT audit and publish `_display_block` render only with a valid
`schema_version` too. A `requirements[]` without `schema_version:1` is a half-activation whose
WARNs silently die. Always emit `schema_version:1` alongside the array.
## Shape
Emitted next to the free-text `acceptance_criteria` (both coexist: strangler):
```json
"schema_version": 1,
"requirements": [
  {"id": "FR-1", "statement": "<what>", "work_class": "user-story|enabler-story|task|spike|bug",
   "source": {"kind": "user-stated|ddd-derived|assumption", "ref": "<the why / origin>"},
   "status": "confirmed|assumed|needs-clarification",
   "acceptance": [{"given": "...", "when": "...", "then": "<measurable>"}]}
],
"clarifications": [
  {"qid": "Q1", "question": "...", "options": ["A. ...","B. ...","X. Other"],
   "answer": "A. ...", "answered_by": "user|self", "resolution": "<self-answer evidence — only for answered_by:self>"}
]
```
(One literal per enum in the real artifact.)
## Numbered parts → `parts` `[GATE·validator]`

When the raw requirement numbers its parts `(1)`..`(n)` (n >= 2, exactly that sequence),
every part must be carried by a requirement (`"parts": [3]` on it) or ruled out (a
`ruled_out` entry starting `(3) <why not now>`). `design_spec.requirement_part_errors`
refuses EVALUATE otherwise (runs from `CONSTRAINTS_CUTOVER`): a part dropped here is
invisible to every later gate that judges against the ACs. Other parenthesised numbers
(a year, `(2)` and `(5)`) are prose and are ignored.

## Field rules (`_check_clarifications` WARNs on each)
- **Canonical fields (SSOT §4.1 + the §4.6.1 audit render = 问题/选项/回答/答者):** `qid`,
  `question`, `options?`, `answer`, `answered_by`. **`answer` is the AUDIT ANCHOR** (what was
  actually picked) and is REQUIRED (the §4.6.1 "回答" column). `resolution` is the OPTIONAL
  self-answer *evidence*, expected only when `answered_by: self`, never the anchor.
- `qid` must be UNIQUE within the array: a duplicate corrupts the audit trail (watch the resume
  re-append path).
- **`source.kind` is SOUL P1 at the requirement layer:** every requirement cites where it came
  from.
- **Self-answered ambiguities go in `clarifications[]` too** (`answered_by: self`), recorded
  identically to user answers and distinguished ONLY by the answerer field, so the §4.6 audit
  trail shows who decided what. Emit `[]` if there were none.
- A requirement upgraded by a user MCQ answer: set BOTH `source.kind`→`user-stated` AND
  `status`→`confirmed`. The validator WARNs on the half-upgrade (kind set, status forgotten or
  absent) and on any user-stated-but-not-confirmed requirement.
## Real-time inline emit (§4.6.4): show the user what you decided, now
After `requirements[]` and the self-answer round (and any MCQ), the user should see a compact
structured block in the chat stream right after the EVALUATE landmark, not only at REPORT.
**Do NOT hand-type this block from memory.** It exists ready-rendered as the `_display_block`
field of a non-`--quiet` `publish --stage evaluate` result (CLI-rendered from this run's
`requirements[]`/`clarifications[]` via `render_audit_trail_md`, the SAME source as the run-end
REPORT §4.6, so preview and archive cannot drift). Relay that field into the ① EVALUATE
landmark output; you transcribe a ready-made block, you do not regenerate one.
> **doc-code drift:** `stage-complete` (the card's exit) does NOT return `_display_block`
> (verified: its result keys are ok/stage/artifact_id/next_stage/next_card/next_action/
> advanced/derived/exit_criteria/report_generated/warnings). On the stage-complete path the
> §4.6 audit surfaces at the run-end REPORT + Pipeline card instead (never lost, a different
> surface). The same holds for a background/headless run that publishes with `--quiet`.
The block's real format is the REPORT §4.6 provenance-audit markdown (`## 需求溯源审计` with the
FR derivation table + 👤/🧠 clarification Q&A + ⚠️ leftover). This sketch only ILLUSTRATES the
information it carries (per-FR source + answerer + load-bearing flags):
```
📋 需求理解(EVALUATE 产出 · 实时)
  FR-1 [👤 user-stated]   <statement>            ✅ confirmed
  FR-2 [🧠 self-answered] <statement>(依据…)     ⚠️ assumed    ← 我替你拍的板,看一眼
  NFR-1 [🧠 ddd-derived]  <statement>            ✅ confirmed
  ❓ 待你确认(若有 clarification-escalate):Q1 …(A/B/X.Other)
  ⚠️ 承重假设:FR-2 若错则 BUILD 返工 → 建议现在确认
```
Rules: (1) **answerer-distinguished:** `👤 user` vs `🧠 self-answered` MUST be visually
distinct; the self-answered ones are what the user most needs to see. (2) **load-bearing
assumptions** (`assumption_weight: load-bearing`, if present) carry the `← 建议现在确认` flag.
(3) The run-end REPORT still carries the full §4.6.1 audit block: the inline emit is an
incremental PREVIEW, the REPORT the complete archive, both rendered from the same data.
## Full artifact example (legacy publish path)
`stage-complete` is the normal exit. The lower-level `publish` + `advance` pair still works and
is the only path that returns `_display_block`. A full evaluation payload looks like this (the
free-text `...` placeholders are illustrative; `schema_version`, `requirements[]` and
`clarifications[]` must be real):
```bash
python backend/scripts/artifact_cli.py publish --project <PROJECT> --run-id <RUN_ID> \
  --type evaluation --producer s_autonomous-pipeline \
  --summary "<GO/DEFER/REJECT>: <one-line>" --stage evaluate \
  --data '{"requirement":"...","scores":{...},"recommendation":"GO","scope":"standard","acceptance_criteria":[...],"schema_version":1,"requirements":[{"id":"FR-1","statement":"<the capability the user needs — a measurable what>","work_class":"user-story","source":{"kind":"user-stated","ref":"<the why / where it came from>"},"status":"confirmed","acceptance":[{"given":"<precondition>","when":"<action>","then":"<measurable outcome>"}]}],"clarifications":[{"qid":"Q1","question":"<ambiguity you resolved>","options":["A. ...","B. ...","X. Other"],"answer":"A. ...","answered_by":"self","resolution":"<self-answer evidence — only for answered_by:self>"}],"understanding":{...},"ambiguity_scan":{"scanned_fields":["who","what","why","when","acceptance_criteria"],"terms_checked":[...],"hits":[...],"hit_count":0,"all_resolved":true},"pre_mortem":["..."],"working_backwards":{"target_customer":"...","current_workaround":"...","why_better":"...","must_be_true":"..."}}'
python backend/scripts/artifact_cli.py advance --project <PROJECT> --state think --run-id <RUN_ID>
```
- `working_backwards` is REQUIRED only when `understanding.work_type=="greenfield"` (+ strict
  profile); omit it for every other work type.
- `--run-id` is mandatory on both commands; the continuation rule on the card applies after
  `advance` exactly as after `stage-complete`.
