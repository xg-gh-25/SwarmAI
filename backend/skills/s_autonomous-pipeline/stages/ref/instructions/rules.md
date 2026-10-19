# INSTRUCTIONS ref — Rules 1–22 and the enforcement-level legend
Read when: a stage card, a validator message or `next_action` cites a Rule number, you are unsure
whether a statement is code-enforced, or you are planning/estimating work (Rule 21). Rules 23–24:
`rules-23-24.md` (Rule 23's bounded-spawn block is on the card).

> **🔖 Enforcement-level tags (used across INSTRUCTIONS.md + all `stages/*.md`).** Every
> rule-bearing statement carries exactly ONE level:
>
> | Tag | Meaning | Bypassable? | Enforced by |
> |-----|---------|-------------|-------------|
> | **[GATE]** | Code-enforced, cannot be rationalized past | ❌ No | ONE of three layers, named in-line: **hook** (`hook_builder.py` PreToolUse gate) · **validator** (`pipeline_validator.py` publish-time BLOCK) · **cli** (`artifact_cli.py` completion/commit-time BLOCK) | <!-- R-instr-015 -->
> | **[MUST]** | Agent discipline — mandatory, but NO code backstop | ⚠️ Yes (only your discipline holds it) | prose only | <!-- R-instr-016 -->
> | **[GUIDE]** | Recommended default; deviation allowed with reason | ✅ Yes | prose only |
>
> <!-- R-instr-017 --> The full **[GATE] → code-enforcement-point** map (which layer backs each
> [GATE], with the concrete symbol) lives in `TECH.md` § "Pipeline gate enforcement map"; each
> [GATE] also names its layer in-line. A statement that reads like a hard block but carries
> **[MUST]** (not [GATE]) is deliberate: NO code enforces it — only you do.

1. <!-- R-instr-018 --> **[MUST] Execute inline, never invoke skills.** You ARE the pipeline. Run
   each stage's behavior directly. Do not use `/evaluate` or `/qa` as slash commands.
2. <!-- R-instr-019 --> **[GATE·cli] Read stage docs before executing.** The card's stage-entry
   rule is BLOCKING — read the stage's execution card (and `stages/ref/` files when the card's
   condition holds); `run-update` refuses a stage whose stage-json lacks `stage_doc_consumed: true`
   (exit 1); `stage-complete` sets the flag for you, so reading the card is still on you.
3. <!-- R-instr-020 --> **[MUST] TDD is mandatory in BUILD.** RED → GREEN → SMOKE → TRACE
   → PROBE. Fix code, not tests. Changing tests = changing the spec. (The `tdd` fields are
   [GATE·validator] at publish — but "did you *actually* RED-first / fix code not tests" is
   discipline, not code-checked.)
4. <!-- R-instr-021 --> **[MUST] Classify every decision.** No unclassified decisions. If unsure,
   default to "taste" (surface at the delivery gate rather than block or ignore). (Validator
   Check 4 is WARN-only, never a BLOCK — so this is discipline, not a gate.)
5. <!-- R-instr-022 --> **[MUST] Verify before advancing.** Run the validator after every stage
   (`stage-complete` runs it; legacy: `pipeline_validator.py check`). (Running it is discipline;
   what the validator then finds is [GATE·validator].)
6. **[GUIDE] Completeness bias.** When the complete implementation costs minutes more than the
   shortcut, do the complete thing.
7. **[GUIDE] Atomic commits.** One commit per logical change in BUILD and TEST.
8. <!-- R-instr-023 --> **[MUST] Never loop forever.** Respect max_retries. Checkpoint on exhaustion.
9. <!-- R-instr-024 --> **[MUST] Taste decisions are recorded, never presented for approval.** Don't interrupt.
10. <!-- R-instr-025 --> **[MUST] Judgment decisions go to the user** (`escalation.md`):
    `channel=direct` → AskUserQuestion at the point you hit it, continue the SAME run on the
    answer; no attended channel → record the question + checkpoint. Never guess an L2 to
    keep going; never ask a Mechanical or Taste decision. (The 4h block is [GATE·hook]
    `ask_question_gate`; the routing is [MUST].)
11. <!-- R-instr-026 --> **[MUST] DEFER/REJECT at evaluate ends the pipeline.**
12. <!-- R-instr-027 --> **[GATE·cli] Always generate REPORT.md** at
    `.artifacts/runs/<RUN_ID>/REPORT.md`. `run-update --status completed` BLOCKS if REPORT.md is
    missing or <500 bytes.
13. <!-- R-instr-028 --> **[MUST] Binary push-ready gate at delivery.** No numeric score. Binary:
    PUSH-READY (all gates pass) or NOT-PUSH-READY (blockers listed). (The `push_ready` flag is
    [GATE·cli]: computed by the tool; false still commits locally, marked, and blocks push; the
    binary-not-numeric *framing* is [MUST].)
14. <!-- R-instr-029 --> **[MUST] Source-path reads.** Always read from `backend/skills/` (source of
    truth), not `.claude/skills/` (projected copy).
15. **[GUIDE] Value over completion.** The purpose is qualified value, not finished stages. A run
    that reaches DELIVER with a working feature beats one that reaches REFLECT with a broken one.
    If BUILD passes tests but doesn't solve the user's actual problem, that is a failure. Does
    this change make the user's life better? If not clearly yes — stop and reassess, don't push
    through to mark done.
16. <!-- R-instr-030 --> **[MUST] Evidence over assertion.** Do not rely on intent, partial
    progress, elapsed effort, or memory of earlier work as proof of completion. The ONLY valid
    proof is external evidence: a test that passes, a file that exists, a command that returns
    the expected output. "I implemented X" is not evidence — `test_x passes` is. "I reviewed the
    code" is not evidence — `3 findings, all fixed` is. Every claim in the Completion Audit must
    cite a verifiable artifact.
17. <!-- R-instr-031 --> **[MUST] No premature completion.** Do not advance to REFLECT or mark
    status=completed unless the Quality Convergence Loop (Step 4c) exits push-ready — all 6 gate
    layers pass, self-assessment positive, goal aligned. Budget pressure is NEVER a valid reason to
    skip convergence OR to checkpoint (STEERING #2; a budget-worded checkpoint is refused by the
    confabulation guard). If you genuinely cannot finish, hand back only via a true trigger (L2
    BLOCK / retries exhausted / stuck). A half-delivered feature with a legitimate checkpoint
    resume plan is better than a "completed" pipeline with hidden gaps.
18. <!-- R-instr-032 --> **[MUST] Adversarial findings must be specific.** Each finding includes
    (a) file path and line number or function name, (b) what's wrong, (c) concrete fix. "Looks
    good", "could be improved", "consider adding" are noise, not findings. Vague sub-agent findings
    → reject and re-prompt: "be specific: file, line, what's wrong, how to fix."
19. <!-- R-instr-033 --> **[GATE·cli] Every stage is mandatory — the pipeline is a DDD loop, not a
    checklist.** The completion gate (`run-update --status completed`) mechanically enforces that
    ALL profile stages are either `completed` or `skipped` (with an explicit `skip_reason`) — a
    hard gate, not an honor system. Each stage serves the loop: EVALUATE reads IMPROVEMENT.md
    (learns from the past) → GO/DEFER; THINK/PLAN read PRODUCT.md + TECH.md → informed design;
    BUILD/REVIEW/TEST → verified implementation; DELIVER packages + audits → qualified delivery;
    REFLECT writes IMPROVEMENT.md (teaches the future). Skipping REFLECT breaks the learning loop;
    skipping EVALUATE may rebuild something already tried and failed.
    **To skip a stage:** `status: "skipped"` + a `skip_reason` saying WHY it is safe. "Budget
    pressure" is NOT a valid reason — CHECKPOINT instead. Valid: "user override: approach already
    known", "profile does not include this stage", "prerequisite output empty".
    **Context exhausted:** CHECKPOINT with the next stage as resume point. Do not compress or skip
    stages to fit the current session.
20. **[GUIDE] Conserve output tokens.** A full pipeline in one response can hit the ~16K output
    token limit and truncate silently mid-stage. Follow the Token Conservation rules (card +
    `progress-display.md`): suppress CLI JSON echo, compact test output, no explanatory prose
    between stages, no preambles for bugfix/trivial. The value is in the CODE committed, not the
    TEXT displayed.
21. <!-- R-instr-034 --> **[MUST] Planning unit = pipeline run.** When estimating work, presenting
    execution plans, or decomposing features, the atomic unit is a pipeline run, not a time-based
    step or a task list with hours. Each run is one independently committable + verifiable
    deliverable. Estimation = number of runs; progress = run pass/fail. Never present "Step 1 (2h):
    do X, Step 2 (1h): do Y" (the human-team model); present "Run 1: {requirement}, Run 2:
    {requirement}" with profile selection. Applies to "what's the plan", EVALUATE scope breakdown,
    multi-feature decomposition, and any talk about execution strategy.
22. <!-- R-instr-035 --> **[MUST] Stage metrics are audit trail — MANDATORY for full/bugfix.**
    Every stage record MUST carry `token_cost` (tokens consumed by this stage). Chat output IS the
    live demo, run.json IS the audit record, REPORT.md IS the projection of run.json — all three
    must agree: a result shown in chat must also be in the stage record. The completion gate WARNs
    (future: BLOCK) on `token_cost: 0`. Always include: `token_cost`; `files_changed` (BUILD);
    `tdd` counts (BUILD/TEST); `decisions` (all stages); gate verdicts — `gate1_verdict` /
    `gate1_checks` (BUILD), adversarial findings summary (DELIVER). On the new path
    `stage-complete` derives `token_cost` (measured), `files_changed`, test layers and finding
    counts for you; on the legacy `run-update --stage-json` path you supply them (token_cost via
    the `checkpoint-and-budget.md` formula). REPORT.md is generated from run.json — an empty
    record yields an empty report.
