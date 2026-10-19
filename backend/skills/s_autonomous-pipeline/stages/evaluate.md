# EVALUATE — execution card
Purpose: record the user's intent as structured requirements, score it, pick the profile, pass Gate 0 (understand the present before THINK proposes a fix).
**Enter:** `python backend/scripts/artifact_cli.py stage-start --project <P> --run-id <R> --stage evaluate` (valid examples + enums).
## Steps
1. **Clarify** before scoring: WHO/WHAT/WHY/WHEN; fill a gap only if DDD derives it (cite), else flag; edge cases; TECH.md Constraints/Runtime Traps. ≥2 unresolvable ambiguities or a conflict → CLARIFICATION-ESCALATE. Then ONE re-scan for hedge terms (`_AMBIGUITY_TERMS`) → `ambiguity_scan`.
2. **Audit** an existing subsystem (OP1-OP8; gaps become ACs). `cross_boundary` (`false` needs `ruled_out`). Drift check.
3. **Score** per `backend/skills/s_evaluate/SKILL.md` (ROI, scope, verdict, ACs); first Anti-Repetition vs IMPROVEMENT.md "What Failed". GO → `pre_mortem`; greenfield + strict → `working_backwards`. ACs pass no-op / user-value / garbage-in filters.
4. **Profile**, first YES wins: only .md/.rst → docs; no code output → research; ≤1 file config/const, no logic → trivial; root = one code line, no prior/sibling → bugfix (`[GATE·validator]`, else full); ITERATIVE target (metric, sweep, N-unknown; not "a test will pass") → goal; else full (incl. work_type `refactor`). `python backend/scripts/artifact_cli.py run-update --project <P> --run-id <R> --profile <profile>`
5. **Gate 0, Understanding Gate:** `understanding` = {work_type, claim (present state, no fix), evidence (an observation), evidence_kind, skeptic_verdict, alternative_considered, **`root_cause`**}: dig past the surface — `why_chain` ≥ 3 `W<n>` to the design level, `root_layer`, `missed_invariant`, `recurrence` (searches, prior, siblings `C<n>`); `ref/plan/design-spec.md` §0.
6. **M3 skeptic** (bugfix/full/goal, after GO, before THINK): file `{"understanding":…, "claims":[≤4 file:line claims to refute]}` → `python backend/scripts/artifact_cli.py gate0-prompt --project <P> --run-id <R> --data @<file>` (exit 0 only). Spawn ONE fresh sub-agent with its `spawn`, foreground. Understanding changed → re-run both. Not SUPPORTED → observe, re-frame, don't advance.
### Structured Requirements (`requirements[]`) [MUST] <!-- R-evaluate-015 -->
**You MUST produce a structured `requirements[]` array** (strict profiles) beside `acceptance_criteria`, plus `schema_version:1` and `clarifications[]` (`[]` if none). Array + version are a non-optional pair: emit both or neither (else the audit no-ops). Validator WARNs only; still required. Do NOT hand-type the preview: relay `_display_block` from a non-`--quiet` `publish --stage evaluate`.
## Gates
- `[GATE·validator]` Understanding: strict profiles BLOCK without real `understanding.evidence` (bug-class: `observation_evidence`, "REPRO gate:"); M1 blocks a fix in `claim`, M2 an unresolved hedge; `root_cause` + bugfix rule.
- `[GATE·validator]` `ambiguity_scan` (strict): each hit's resolution ≥12 chars; `hits: []` valid.
- `[GATE·validator]` migration keyword → `migration_class`, `enumeration_cmd` greps a physical sink.
- `[GATE·validator]` greenfield + strict → `working_backwards` + non-empty `pre_mortem`.
- `[GATE·artifact_cli]` `cross_boundary=true` needs a passing TEST Layer-4 E2E before DELIVER.
- `[MUST]` M3 skeptic, health audit, Anti-Repetition, AC filters, `pre_mortem` on GO.
- `[MUST]` DEFER/REJECT ends the run; self-answer every ambiguity you can; a genuine one (≥2 readings, none clearly safer) → ask the user (`ref/evaluate/clarification.md`), SAME run.
## Read only if (under `stages/ref/evaluate/`)
- ambiguity, conflict, any escalation exit → `clarification.md`
- `understanding`, `refactor`, migration, skeptic brief, "Understanding gate:" → `understanding.md`
- subsystem audit, `cross_boundary`, prior run, `code_intel.db`, "What Failed" → `audits.md`
- goal vs full, `pipeline_intelligence.json`, goal mode → `profile.md`
- ACs, GO verdict, greenfield → `acceptance.md`
- requirement fields, MCQ answer, preview → `requirements.md`
## Exit
### Artifact Publish
`python backend/scripts/artifact_cli.py stage-complete --project <P> --run-id <R> --stage evaluate --data @/tmp/<R>/evaluate.json`; file shape (inline works too):
--data '{"artifact":{"recommendation":"GO","scope":"standard","schema_version":1,"requirements":[{"id":"FR-1","statement":"<what>","work_class":"user-story","source":{"kind":"user-stated","ref":"<why>"},"status":"confirmed","acceptance":[{"given":"<g>","when":"<w>","then":"<t>"}]}],"clarifications":[]},"record":{"decisions":[]}}'
> **⏭️ Continuation — advance is not the end of your turn.** Read `next_card` and run that stage **in this same turn**. Legal hand-backs only: an L2 or unclear-requirement question (ask, or checkpoint), a user stop, or a terminal verdict (`ref/instructions/escalation.md`). `next_action` is AUTHORITATIVE: if it withholds the continue instruction, follow it.
