# EVALUATE ref — Understanding Gate (Gate 0), migration_class, the M3 skeptic
Read when: writing the `understanding` block, classifying `work_type = refactor`, the
requirement contains a migration keyword, briefing the skeptic, or publish returned an
"Understanding gate:" / "REPRO gate:" error. The card (`stages/evaluate.md` steps 5-6) has the
core procedure.
### Understanding Gate `[GATE·validator]` (ALL work types — understand the present before proposing a fix) <!-- R-evaluate-002 -->
`NO BUILD WITHOUT AN OBSERVATION-BACKED, REFUTED UNDERSTANDING OF THE PRESENT`
**Trigger:** EVERY evaluation, not just bug fixes. Before THINK proposes *how* to fix or build,
EVALUATE must produce a falsifiable claim about the **current state of the world**, backed by
an **observation** (not inference), that has **survived a refutation attempt**. The *form of
evidence* varies by work type; the gate itself is universal. A confident-but-wrong frame
otherwise sails through THINK/PLAN/BUILD and is caught only at the Gate-2 adversarial (full
pipeline cost) or by an external reviewer.
**The universal `understanding` block (in the evaluation artifact):**
```json
"understanding": {
  "work_type": "bugfix | existing-feature | greenfield | refactor | research | docs",
  "claim": "A falsifiable statement about the CURRENT state (present-tense, NOT a plan)",
  "evidence": "An OBSERVATION supporting the claim — form varies by work_type (below)",
  "evidence_kind": "observation | code-trace | repro | premortem | characterization",
  "skeptic_verdict": "SUPPORTED | UNSUPPORTED | ALREADY-SATISFIED | WRONG-FRAME",
  "alternative_considered": "The simplest competing framing, and why it loses"
}
```
(Each enum is one literal in the real artifact; `stage-start` shows the valid examples.)
The **claim is about the present, not the plan.** "The spinner hangs because `onComplete`
early-returns on a stale `streamGen`" is a claim about the present. "I will add a per-tab
`latestCompleteGen`" is a plan; it belongs in THINK, on the other side of the wall.
**Evidence form varies by work type (the key):**
| `work_type` | "Understanding" means | Required `evidence_kind` | Concrete form |
|-------------|----------------------|--------------------------|---------------|
| **bugfix** | the **root cause** | `observation` / `repro` | `ps` output, log-signal counts, live gauge read, a reproduction *(the REPRO gate)* |
| **existing-feature** | how the **current system actually works** + where the change fits | `code-trace` | the real call path / data flow read from source (cite file:line), NOT "I assume it works like…" |
| **refactor** | the **current behavior that must be preserved** | `characterization` | a characterization test or an input→output matrix traced against `git show HEAD` |
| **greenfield** | the **problem and who has it** | `premortem` | problem statement + "top 3 reasons this fails" |
| **research** | the **actual question** + what's known | `premortem` (scope) | the question restated falsifiably + what makes it a wrong/unanswerable question |
| **docs** | what's **true about the code** being documented | `code-trace` (relaxed) | file refs to the code each doc claim describes |
## Classifying `work_type = refactor` (sets downstream rigor; choose it deliberately)
Two gates key off it: Gate-1 SSA (`build.md` Check 4) INVERTS its PATCH polarity (for a
refactor a patch is a BLOCK, because the structural change IS the acceptance criterion), and
DELIVER dispatches the architecture-relevant specialist set instead of the narrow bugfix pair.
Because of those teeth the trigger is **NARROW**: classify `refactor` only when the requirement
itself asks to *restructure*: "de-patch / stop patching X", "make X sustainable /
maintainable", "root-fix the architecture", "unify the N copies", "remove the whole class of
Y". Behavior is preserved but the STRUCTURE is the deliverable.
- Do **NOT** classify `refactor` merely because a change touches >1 file or renames things. A
  bounded feature or a symptom-fix with a known root cause is `existing-feature` / `bugfix` and
  gets the default (patch-tolerant) rigor.
- Unsure between `bugfix` and `refactor`? Ask: *did the user ask me to fix a behavior, or to
  fix the structure?* Structure → `refactor`.
- **`refactor` is a WORK_TYPE, not a profile.** It routes to the **`full`** profile (identical
  stage set; the profile decision tree has no refactor node, by design). The refactor rigor
  rides on `work_type=refactor`, exactly as greenfield's Working-Backwards rigor rides on
  `work_type=greenfield` without being its own profile.
## `migration_class` — mandatory Gate-0 block for a class-migration requirement
A goal run that migrates a *class* (all callers of a chokepoint, all siblings of a type) across
cycles has a blind spot no per-cycle adversarial can see: a class member NO cycle touches is
never in any diff. The class-completeness gate (goal_cycle Final Quality Gate step 2.5) closes
it, but ONLY if the run declares a `migration_class`.
> **Gate-0 rule `[GATE·validator]` (`_check_migration_class` + `check_migration_class.py`, publish-time BLOCK):** if the requirement contains a <!-- R-evaluate-003 -->
> migration keyword — **migrate / unify / consolidate / de-dup / "route … all" /
> "gate … all" / "every … through" / "single … for all"** — the evaluation artifact MUST
> include a `migration_class` block. Opt-in was the escape hatch: an agent with an
> incomplete mental model simply omits it → the gate no-ops → the exact miss ships. Making
> it mandatory-on-keyword removes that hatch. A keyword-free requirement needs no block
> (the gate no-ops, zero false-positive).
**Shape** (the design doc has the full contract):
```json
"migration_class": {
  "description": "<the class in one line>",
  "enumeration_cmd": "grep -rn '<sink_fn>(' backend/ | grep -v 'def '",
  "members": [{"id","disposition":"migrated|carved-out","locator":"file:line","evidence":"symbol|reason"}]
}
```
**`enumeration_cmd` MUST grep a PHYSICAL SINK across the tree (R-A), NOT a hand-listed set.** A
self-authored member list inherits the same blind spot that caused the miss.
`scripts/check_migration_class.validate_enumeration_cmd` REJECTS an echo/printf literal list or
a single-file-scoped grep. Enumerate the sink (the last-mile write call every member is forced
through); let the grep find the files, don't curate them.
## Three mechanisms <!-- R-evaluate-004 -->
**M1+M2 `[GATE·validator]`, M3 `[MUST]` spawn + `[GATE·cli]` verdict-to-spawn binding:**
- **M1 — Separation (the wall). `[GATE·validator]`** The `claim` must describe the PRESENT, not
  a fix. The validator BLOCKS a claim containing solution language ("I will / the fix is /
  add … / refactor …"). THINK is the first stage allowed to propose a fix.
- **M2 — Observation-not-inference (R16b mechanized). `[GATE·validator]`** A hedge in the claim
  or evidence (`似乎 / 可能 / probably / should be / I think / likely`) BLOCKS unless the
  `evidence` is a concrete, non-hedged observation that resolves it. Validator-enforced.
- **M3 — Refutation (the skeptic). `[MUST]`** (spawn the Agent tool, same pattern as
  deliver.md's adversarial gate). The spawn itself is behavioral, but a recorded
  `skeptic_verdict` is `[GATE·cli]` (runs created from 2026-10-18T16:00Z): `stage-complete
  --stage evaluate` refuses it unless a `gate0-skeptic` sub-agent of THIS run completed
  during this EVALUATE (the emitter's prompt starts `EXPERT-ID: gate0-skeptic`; keep that
  line FIRST, within the prompt's first 2000 chars, when you add your claims). A spawn that
  ran but cannot be proven (no audit hook in this session) → record
  `"gate0_unverified_reason": "<why>"` in the evaluate record. For bugfix/full/goal, after scoring a GO and BEFORE advancing to THINK, spawn ONE
  fresh-context sub-agent with ZERO of your reasoning. Its job is to REFUTE. Get the prompt
  from the emitter (it lives in `data/gate-prompts/gate0-skeptic.md`; emit it, do not retype
  it), run from the repo root:
  `python backend/skills/s_autonomous-pipeline/scripts/spawn_prompt.py --gate gate0-skeptic`
  Use the output ONLY on exit 0. Exit 2 means the prompt states no numeric cap and stdout is
  deliberately EMPTY, so pasting it blindly would spawn with no prompt at all.
  Spawn it with `run_in_background: false`. The Agent tool defaults to background, and a
  background spawn ends this turn's visible stream: the user sees nothing running and no
  status until a notification arrives. Wait for the verdict in the same turn.
> **⚠️ Caller-side rule: do the investigation YOURSELF, delegate the JUDGEMENT.** Hand the
> skeptic a CONCLUSION to attack (your claim + the file:line evidence you already read), never
> a research assignment. A prompt that tells the sub-agent to "verify each of these / grep and
> confirm / do not take on trust" re-runs your own investigation in a slower process AND
> transfers your judgement cost onto it. A skeptic prompt carrying a long list of numbered
> verify-this-yourself sub-questions searches for a very long time and finds little; the same
> claim restated as "confirm or refute these 3 specific things" returns in seconds and still
> catches errors the orchestrator missed. Bound the ITEM COUNT (N ≤ 3-4 concrete claims), and
> never ask the sub-agent to prove a negative ("prove nothing is wrong" has no termination
> condition).
**Route on verdict:**
- **SUPPORTED** → proceed to THINK. Record the cited evidence in `understanding.evidence`
  (bug-class evals may use the legacy `observation_evidence` alias; both satisfy the validator).
- **UNSUPPORTED / ALREADY-SATISFIED / WRONG-FRAME** → do NOT advance. Go OBSERVE (code-trace the
  real path, run `ps`, count the log signals, attempt a repro) and re-frame. The cheapest
  insurance against building the wrong thing.
## Rigor tiers (cost-proportionate, design §3.5)
| Profile | M1 wall | M2 hedge-scan | M3 skeptic sub-agent |
|---------|:-------:|:-------------:|:--------------------:|
| trivial | ✅ | ✅ | ❌ (claim + scan only) |
| docs | ✅ | ✅ | ❌ (code-trace refs only) |
| research | ✅ | ✅ | 🟡 scope-challenge (refute the *question*) |
| bugfix / full / goal | ✅ | ✅ | ✅ |
Trivial/docs/research are NOT *forced* to carry the block (anti-ceremony), but if a block IS
present, M1+M2 still scan it. Strict profiles (full/bugfix/goal) REQUIRE the block with real
evidence. The trivial-profile adversarial (Gate-2) remains the back-stop for relaxed profiles.
## Relationship to the validator
M1+M2 and the presence requirement are *code-enforced* by
`pipeline_validator.validate_artifact_data` at publish time (the "Understanding gate:" markers;
the bug-class subset keeps the "REPRO gate:" marker via the `observation_evidence` alias: a
`scope=bugfix` eval on a strict profile is BLOCKED until it records what was OBSERVED). M3 (the
skeptic) is the *spawned* verifier that produces the verdict the field records. Model
proposes, the gate disposes.
