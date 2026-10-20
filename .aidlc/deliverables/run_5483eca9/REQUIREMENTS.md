# Requirements

**Run** `run_5483eca9` · **profile** ? · **project** SwarmAI

> 起 pipeline：V1–V5

_Generated from the run's stage artifacts by the pipeline (not hand-written)._

**Verdict** GO · **Scope** complex · **Scores** strategic 8, priority 8, feasibility 7

## Requirements

| ID | Requirement | Source | Status |
|---|---|---|---|
| FR-1 | Repair the audit chain breakpoints D3, D5, D6, D7, D8 so each stage's hand-off reaches the next stage and the committed run documents. | user-stated | confirmed |
| FR-2 | The PLAN spec carries a consumers section (contract, rg pattern, paths, readers) or a reason there is no contract change, rendered in DESIGN_SPEC.md. | user-stated | confirmed |
| FR-3 | A deterministic function-level consumers gate at BUILD completion and after each Gate-2 fix before re-check. | user-stated | confirmed |
| FR-4 | A CI lint against dropping the problem value of registered (value, problem) loaders. | user-stated | confirmed |
| FR-5 | Requirement-to-AC trace: numbered requirement parts map to requirement ids or ruled_out; spec-compliance sees the raw requirement. | user-stated | confirmed |

**FR-1 acceptance**
- Given a run whose PLAN has a design and security_boundaries · when Gate-1 prompt and DESIGN_SPEC.md are written · then both contain the design / the four sections
- Given a Gate-2 finding with fix and exploit · when the SubagentStop hook records it · then the marker row keeps both

**FR-2 acceptance**
- Given a strict plan with neither consumers nor no_contract_change_reason · when stage-complete --stage plan · then it is refused with a path to the missing field

**FR-3 acceptance**
- Given a declared pattern with a hit in a function the diff did not change and that is not listed · when build stage-complete runs · then it refuses naming file::function

**FR-4 acceptance**
- Given code `x = pv._load_evidence_json(p)[0] or {}` · when the lint runs · then it fails naming file:line

**FR-5 acceptance**
- Given run_6238b7c3's requirement and evaluation · when the trace check runs · then part (3) is reported unmapped

### Acceptance criteria

- AC1 (part 1, D3): the Gate-1 prompt written by _write_gate1_prompt contains the PLAN design invariants, failure_modes and states (fenced as data).
- AC2 (part 1, D5): DESIGN_SPEC.md rendered by run_docs.render_design_spec shows sections for security_boundaries, boundaries, test_strategy and success_criteria when the plan artifact carries them; absent fields render nothing; odd shapes never raise.
- AC3 (part 1, D6): the gate1-skeptic SubagentStop marker keeps the skeptic's BLOCK/WARN lines, and the REVIEW.md Gate 1 row shows them beside the verdict.
- AC4 (part 1, D7): runtime_hooks._finding_row keeps the fix, exploit and evidence fields of a finding (bounded length); a finding without them is unchanged.
- AC5 (part 1, D8): build_injection_recommendations has 0 references in backend/ (code, skill docs, tests); its input rp_violations is never populated, so it always yields [].
- AC6 (part 2): the PLAN artifact accepts implementation_constraints.consumers [{id, contract, pattern, paths, readers[{at, status, reason?}]}] or no_contract_change_reason; plan stage-complete on a strict profile refuses a plan with neither, and DESIGN_SPEC.md renders the consumers table.
- AC7 (part 3): at BUILD stage-complete and after a Gate-2 fix before its re-check, for each declared pattern every enclosing Python function of an rg hit that the run's diff did not change must be listed in readers as unaffected with a reason; otherwise the command refuses and names file::function. Fixture from run_2702d56e: the unmigrated prove_calls site and the unmigrated run_index readers are named.
- AC8 (part 4): a CI lint fails on code that drops the problem value of a registered (value, problem) loader by defaulting it ([0] or <default>, or unpacking the problem into _); it passes on reads that test the value for None / isinstance (the current tree is clean).
- AC9 (part 5): EVALUATE stage-complete refuses when the raw requirement numbers its parts (1)..(n) and some part maps to no requirement (requirements[].parts) and is not listed in ruled_out; fixture from run_6238b7c3 names part (3). Every REVIEW expert prompt (spec-compliance included) contains the raw requirement.

## Clarifications

| Q | Question | Answer | Decided by |
|---|---|---|---|
| Q1 | What are the skeptic's 'verdict points' for the REVIEW.md Gate 1 row? | The BLOCK / WARN lines of the gate1-skeptic final answer, captured by the hook (bounded), as root-cause verdict_text already is. | self |
| Q2 | Which languages get function-level mapping in part (3)? | Python via ast (enclosing def / class method). A hit in a non-Python file maps to the file itself and must be listed at file level. | self |
| Q3 | Does part (4) forbid every [0] on a loader result? | No: only defaulting the value (`[0] or <x>`) or unpacking the problem into `_`. Reads that test the value for None / isinstance stay legal, since an unusable file is then not evidence (fail-closed). | self |

## Understanding (Gate 0)

- **Present state:** In the current pipeline each stage receives a different subset of what earlier stages know, and the recurring defect classes found at Gate-2 (unmigrated readers, dropped problem values, dropped requirement parts) are guarded only by prose: no deterministic check binds them to the run's artifacts, and several hand-offs never reach the committed audit documents.
- **Evidence:** Code trace: every REVIEW expert prompt, spec-compliance included, gets PLAN ACs and the diff but not the raw requirement (artifact_cli.py:11644 'You see ONLY the raw acceptance criteria', builder around 11655-11700); Gate-1 plan_part omits design (artifact_cli.py:11508-11509); render_design_spec (run_docs.py:371) renders no security_boundaries/boundaries/test_strategy/success_criteria; _finding_row keeps only 6 fields (runtime_hooks.py:1613-1622); R-build-014 grep-all-readers is 'agent-discipline, no validator enforces them' (stages/ref/build/pre-checks.md:56); build_injection_recommendations…
- **Alternative framing considered:** The simpler framing is that the builder lacks coding steering (XG's first hypothesis). The replay refutes it as the main cause: only 32% of issues on runs not used to draft the checklist were checklist-covered, and the rules already sat in context (R27, the loader docstring) when the defects were written; deterministic checks bound to artifacts are what the evidence supports.
- **Gate-0 skeptic:** SUPPORTED

## Root cause

W1. Gate-2 keeps finding defects of classes earlier stages had the information to catch: readers left unmigrated inside touched files, a problem value dropped, a requirement part never turned into an AC.  
   _evidence:_ design doc §10.1 replay: I5/I11 (run_2702d56e), I3 + L6 (run_2702d56e, run_1cce404a), J1 (run_6238b7c3)
W2. Each hand-off passes a subset and no stage checks the cross-stage invariant: BUILD gets no reader list, spec-compliance gets ACs but not the requirement, Gate-1 gets no design, and the hook drops finding fields before they reach the ledger.  
   _evidence:_ artifact_cli.py:11644 + 11655-11700 (REVIEW prompts), 11508-11509 (Gate-1 plan_part); runtime_hooks.py:1613; cmd_stage_start out keys
W3. The pipeline was designed to control these classes with rule text (pre-checks.md R-build-014, the _load_evidence_json docstring, AC derivation without a trace) rather than with deterministic checks bound to the run's own artifacts.  
   _evidence:_ pre-checks.md:56 'no validator enforces them'; pipeline_validator.py:5279 'Callers must not treat the third case as no evidence' (prose only); IMPROVEMENT.md:150 and :257 record the reader-migration class recurring

- **Root layer:** module-design
- **Missed invariant:** A defect class that recurs across runs is enforced by a deterministic check bound to the run's artifacts, and every cross-stage hand-off is visible in the committed run documents.
- **Searched:** design doc §10 replay over 4 runs / 33 issues; IMPROVEMENT.md What Failed grep consumer|reader; git log -S'build_injection_recommendations' --oneline; grep -rn '_load_evidence_json(' backend
- **Prior instances:** 3:
  - IMPROVEMENT.md:150 Gate-1 caught an incomplete consumer list (run_ab157198)
  - IMPROVEMENT.md:257 4th listener missed by the plan (run_e626e121)
  - run_1cce404a L6 repeats run_2702d56e I3's [0] or {}
- **Siblings (same shape elsewhere):** 4:
  - C1 artifact_cli.py:11644-11700 REVIEW expert prompts — every REVIEW expert reviews against derived ACs only, never the raw requirement
  - C2 artifact_cli.py:11508 _write_gate1_prompt plan_part — the gate judging the plan does not see the plan's design
  - C3 run_docs.py:371 render_design_spec — artifact fields that never reach the committed document
  - C4 runtime_hooks.py:1613 _finding_row — a whitelist that silently drops fields between expert and ledger

## Pre-mortem

- The function-level gate false-blocks on broad patterns (hundreds of hits) and the builder learns to write narrow patterns that pass: pattern quality stays a review concern; the gate only guarantees listed-or-changed.
- runtime_hooks changes only take effect after a daemon rebuild + restart, so the Gate-1 text and finding fields look missing in runs until deploy; tests must drive the hook functions directly.
- The (n) part parser misreads parenthesised numbers in prose (years, versions) and blocks EVALUATE; it must only act on a contiguous 1..n sequence.
