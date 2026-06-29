---
title: "After `assert` Breaks Down — SwarmAI's Eval Architecture and Methodology"
created: 2026-06-28
updated: 2026-06-28
status: published
---
<!-- GitHub Discussion #83: https://github.com/xg-gh-25/SwarmAI/discussions/83 -->
# After `assert` Breaks Down: SwarmAI's Eval Architecture and Methodology

> How an Agent OS turns "evaluation" from "a script you run once in a while" into "a hard gate wired into the development lifecycle" — plus the methodology behind it, the sources, and the trade-off behind every gate.

---

## TL;DR

Traditional software relies on `assert` + a CI red/green light to guarantee "no regressions." Agents can't — **non-determinism** breaks `assert`, **prompt-as-source-code** has no diff/review/rollback, and **dependencies drift on their own** (the model updates silently; you deployed nothing, but behavior changed).

SwarmAI's answer: **Eval is the Agent-era replacement for `assert`, and it must be a git-bound hard gate.**

This piece covers three things:
1. **Architecture** — what Eval looks like as a **system-level subsystem decoupled from DDD**
2. **Methodology** — how to do Agent eval (mapped to AWS's official Eval-First two-pillar framework)
3. **Gate trade-offs** — where the gate sits, what it binds to, its three-state semantics, and why build doesn't block but release does

---

## 1. Why Agents Need to Redefine "Testing"

Source: AWS's official *Eval-First: Enterprise Agents with AgentCore* (Summit 2026-06, [public repo, MIT-0](https://github.com/aws-samples/sample-eval-first-building-enterprise-agents-with-agentcore)). Its thesis in one line:

> **The bottleneck to shipping agents to production isn't model capability — it's the lack of a sustainable engineering system to measure "how good it actually is."**

Three root causes that break traditional software engineering on agents:

| Root cause | Traditional software | Agent |
|------|----------|-------|
| **Non-determinism** | `assert x == 5` always holds | Even temp=0 isn't bit-reproducible (GPU float non-associativity, MoE routing) → `assert` dies, only eval works |
| **Prompt-as-source** | Code changes have diff/review/rollback | Editing one prompt line = changing code, but with zero version control → every prompt change must run eval |
| **Dependency drift** | Lock the version, stay stable | The model vendor updates silently; you deployed nothing, behavior changed → you need a continuous baseline |

This is why SwarmAI treats Eval as **the Agent-era implementation of the TDD pillar in the AIDLC methodology**: when `assert` breaks, **Eval takes over as the deployment gate**.

---

## 2. Architecture: Eval as a System-Level Decoupled Subsystem

### 2.1 The single most important decision

The whole architecture is decided by one sentence (the product owner's directive):

> **"Eval should be a standalone system-level subsystem with no direct dependency on DDD — we only have one standard golden-case skill and one extraction mechanism."**

Why does this reorganize everything? Because previously eval was **welded to the governance system** via tags like `affected_by: STEERING.R1` plus reading DDD content at runtime. That coupling caused three diseases:
- **Can't share it** — someone who clones it doesn't have your `STEERING.R1`, so the case can't run
- **Leaks governance structure** — internal governance references end up in the public repo
- **Gate goes stale easily** — change DDD, and the gate is instantly stale

Solution: **cut the umbilical cord.**

### 2.2 The decoupling invariant (the spine of the architecture)

> **A golden case is self-contained.** Everything it needs to reach a verdict lives inside the case (scenario, expected outcome, verification command, assertions). **The public golden set and the git-bound gate path reference no governance documents at all** — delete the entire `Projects/` directory, delete all DDD, and `Eval/`'s public cases + CI gate still reach correct verdicts.

**One boundary I have to state plainly (otherwise it's a lie):** the LLM-judge *capability* path **does** read live STEERING/SOUL/AGENT at verdict time — and this is **intentional**: the judge needs to know which rules the agent actually follows in order to judge "would it be compliant?" (`eval_runner.py:_load_rules_context`, the judge prompt comment reads verbatim *"so it knows what rules exist"*). That path runs **only in nightly monitoring** and is **never a gate**. So "decoupling" holds in a more precise, two-part form: **(1) public cases have zero governance references; (2) the gating `ci_eval_gate` only checks digest+report, never reads DDD.** The judge capability path reading live rules is by design, not coupling.

The success metric (executable, pointing at files that actually exist):
```bash
# public golden set references no governance docs → returns 0 = shareable, clone-and-run
grep -rE 'STEERING|MEMORY\.md|SOUL\.md|/TECH\.md|/PRODUCT\.md' Eval/golden_set.yaml   # = 0 ✅
# the gating path doesn't read DDD (pure digest + BVT check)
grep -cE 'STEERING|MEMORY\.md|/TECH\.md' backend/scripts/ci_eval_gate.py              # = 0 ✅
```

### 2.3 Physical structure

```
SwarmWS/                          ← the daemon's working dir (data lives here)
├── Projects/                     ← DDD domain knowledge (eval does NOT depend on it)
└── Eval/                         ← top-level peer, sibling to Projects/. All eval is self-contained
    ├── golden_set.yaml           ← 33 public cases (git-tracked, reference public-repo code)
    ├── golden_set.private.yaml   ← 151 private cases (gitignored, reference this instance's state)
    └── EvalHistory/              ← run reports (gitignored)
```

Code side (public repo):
| Component | Location | Responsibility |
|------|------|------|
| **Eval Runner** | `backend/scripts/eval_runner.py` | Execution engine: programmatic + LLM judge |
| **Eval Service** | `backend/core/eval_service.py` | In-memory cache, API layer, privacy redaction |
| **Git-bound Gate** | `backend/scripts/ci_eval_gate.py` | Pure check (zero Bedrock cost): digest + BVT |
| **Case intake** | `s_golden-case` + `golden_case_validator.py` | The only sanctioned path to add a case, 4 quality gates |
| **Dashboard** | `desktop/src/pages/EvalDashboard.tsx` | 7-tab UI |

### 2.4 The bridge: a one-way wire

DDD and eval are **decoupled but not disconnected** — they're bridged by a **tool**, not a dependency. Direction is everything:

```
   DDD / corrections / session  ──[extract]──►  self-contained golden case  ──►  eval runs it
                              (s_golden-case)    (no DDD refs inside)        (reads no DDD at runtime)
```

- **Extraction is write-time and one-way.** When a correction/lesson/rule-hint needs a new case, `s_golden-case` (or the auto-seed hook) **reads** DDD to author the case — then **bakes the needed context into the case as literal text**. The case carries its own expected behavior, not a "pointer to STEERING.R1" for eval to resolve later.
- **Result:** the extraction mechanism can be as DDD-aware as it likes (it's our authoring tool); the eval runtime stays pure. **The bridge is a skill, and the skill only writes.**

### 2.5 Public/Private split = decoupling as a security boundary

The discriminator is one question: **what does this case depend on?**

| | public (tracked, shareable) | private (gitignored, this instance) |
|--|--------------------------|------------------------------|
| Tests what | Framework / code invariants / deterministic behavior | This SwarmAI instance's own state (my MEMORY/STEERING/rules) |
| Self-contained? | Fully — anyone clones and runs | Asserts against local instance files |
| Privacy | Scanned, no sensitive terms | Never leaves the machine |

**Fail-closed three layers (to prevent a privacy-leak recurrence):**
1. **Directory-level gitignore** — the entire private file is ignored (structural)
2. **Private by default** — new cases (including auto-seed) land private; promotion to public is an explicit action that runs a privacy scan (behavioral)
3. **Redaction uses an allowlist, not a denylist** — `get_case_detail` only **allowlists** safe metadata for private cases. A denylist leaks every newly-added field by default; an allowlist fails closed (an unanticipated field is dropped, never exposed)

Misclassification **fails toward safety**: a misjudged case stays private (you lose a little shareability) — it never leaks (which would be the disaster). **Public is earned, not the default.**

> 💡 **A blood-and-tears lesson:** doing the privacy redaction, I first wrote a denylist of 6 sensitive fields, confident it was complete. Adversarial review caught that it leaked **twice** — `expected_response_contains` (answer keywords) and `source` (governance references) were both missing from the list, and the latter leaked through a **completely different list endpoint**. The lesson hardened into a rule: **any security redaction must use an allowlist, not a denylist; LIST and DETAIL are two independent leak surfaces, and a privacy review must enumerate every endpoint that returns the object.**

---

## 3. Methodology: How to Do Agent Eval

This section maps directly to the **hardest, most reusable** design in AWS's official methodology: the **two-pillar framework**.

### 3.1 Pillar one · Three granularities (mapping to session/trace/span)

| Granularity | Tests what | Distance from user |
|------|--------|--------|
| **Black-box** | Final response (relevance, completeness, tone, correctness) | Closest |
| **Glass-box** | Full trajectory (which **step** went wrong: tool choice/params, efficiency, hallucination) | Middle |
| **White-box** | Single step / single tool call (finest attribution) | Furthest |

Rule of thumb: **score by outcome, give partial credit, attribute via trajectory rather than exact-sequence matching.**

### 3.2 Pillar two · Three evidence-weight tiers (the most overlooked, most critical)

> "A score has consequences" — so the **weight** of a score must be explicit.

| Tier | Meaning | Strength |
|----|------|------|
| **L1 mechanically verifiable** | Pure code, zero ambiguity (schema, format, latency, cost) | Strongest evidence, audit-defensible |
| **L2 semi-objective** | Model-scored, but **only under a pinned evaluator** (fixed model+prompt+temp+seed) | Medium |
| **L3 subjective** | No stable scorer ("is it creative enough?") | **Reject by default** — flag it in the rubric, don't fabricate fake numbers |

**The two pillars are orthogonal → a 3×3 matrix.** "Choosing metrics = picking the cells for your business, not turning them all on."

### 3.3 Three scorer types (fill the matrix, align to the evidence tier)

- **Code-based** (L1, prefer it; never hand a code-decidable check to a judge)
- **LLM-as-a-Judge** (L2, **must do bias mitigation + human calibration**)
- **Human** (L3, scarce resource; spend it on golden-set labeling + pre-release spot checks)

**LLM-judge biases (don't use it naked):**
- **Position bias** (swap A/B → verdict flips) → mitigation: **bidirectional scoring**, judge both (A,B) and (B,A); disagreement = tie
- **Verbosity bias** (long answers over-rated)
- **Authority bias** (a fabricated citation fools most judges)
- **PoLL (Panel of LLM judges)** = multiple judges from different model families, ~1/7–1/8 the cost of one big judge

### 3.4 Capability eval vs Regression eval (we used to conflate these — biggest takeaway)

This is **the distinction you should think through first** in the whole methodology:

| | Capability eval | Regression eval |
|--|----------------------|----------------------|
| Starting score | Low, "a mountain to climb" | Near 100% |
| Purpose | Drive improvement | Hold the baseline, prevent regressions |
| Runs where | nightly, **never gates** | **gates push/build/CI** |
| Shape | A score that climbs over time | Binary: all green, or block |

**A mature capability case "graduates" into the regression suite → into CI.**

> This distinction directly resolved our old `score=0.0` ambiguity ("never ran" vs "all errored" vs "all failed" collapsing into the same 0.0). The gate is binary (any fail/error = red); capability scores are reported separately.

### 3.5 Where we stand (verified against code)

| AWS concept | Our implementation | Status |
|----------|-----------|------|
| ADLC flywheel | s_autonomous-pipeline + evolution loop + DDD cultivation | ✅ Mature |
| Three granularities | trajectory_capture (glass) + keyword/goal (black) + tool-strict (white) | ✅ All three |
| L1 mechanical | runtime_health, file_contains, canary_pass, trajectory_* (programmatic) | ✅ Strong |
| L2 pinned judge | `eval_runner.py` judge pinned, T=0.0 | ✅ Have it |
| L3 reject-by-default | No explicit "refuse to score" tier | ⚠️ gap |
| Capability vs regression split | Now split via a BVT-derived view | ✅ (done this round) |
| Golden set = IP | golden_set 184 cases (33 public + 151 private) | ✅ Strong = our PRI01 |
| Bias mitigation (bidi/PoLL) | Single judge, no bidi, no panel | ❌ gap (deferred) |
| Onto CI | git-bound gate designed, programmatic subset zero Bedrock cost | ✅ (done this round) |

Honest about the gaps: the L3 reject tier, bidirectional judge, and PoLL are all not done yet — they're deferred "legibility-track" items that don't touch the core gate.

### 3.6 Golden Set = Eval IP

> "You can buy the eval platform; you must own the eval content."

Real production data + expert labeling, 4 scenario classes (common/edge/compliance/escalation), failed cases flow back in. **Start from ~20, look at the data before scoring** (error analysis: cluster failures → distill the rubric). Path: 20→100→500. Anti-patterns: rubric-before-data, deploying an LLM-judge too early, a frozen golden set, synthetic data masking reality.

---

## 4. Gate Trade-offs: The Part Most Worth Explaining

A gate's entire value is in **where it sits, what it binds to, and when it lets through**. We've stepped on the rakes; every trade-off has a cost.

### 4.1 What to bind: bind INPUTS, not HEAD

**The fatal bug in v1** (caught by adversarial review): the gate criterion was written as `report.git_commit == HEAD`. But the report itself is a git-tracked file — commit the report and HEAD advances, so `git_commit != HEAD` → **the gate is permanently red.** A self-contradicting fixed point.

**The right way: bind to the *inputs* eval depends on (code + golden_set), not which commit it lives in.** Like `uv.lock` hashing its inputs rather than hashing itself:

```json
{
  "code_digest": "<sha256 of git ls-tree over eval-relevant paths + golden_set content>",
  "tree_dirty_at_run": false,
  "bvt": { "total": N, "passed": N, "failed": 0, "error": 0, "green": true }
}
```

**Why digest-of-inputs:**
- Commit the report → `EvalHistory/` isn't in the digest → digest unchanged → gate stays green (fixes the fixed-point)
- Change `golden_set.yaml` (which *defines* what the gate tests) → digest changes → report stale → gate blocks (closes the "quietly edit a case to bypass a green gate" hole)
- Change docs/context → doesn't touch the digest → freshness unaffected (no "path-filter × freshness" contradiction)

`code_digest` uses `git ls-tree` (not byte-wise hashing) — respects `.gitignore`, O(1) subprocess, and **scopes only eval-relevant paths**, not all code. Because public cases don't reference DDD, the digest never depends on DDD → **changing STEERING never staleens the gate. Decoupling is precisely the prerequisite that makes a scoped digest correct.**

### 4.2 Who gets in: BVT is a derived view, not a hand-maintained list

**BVT (Build Verification Test) = the regression gate set**, defined as a **derived view** (anti-rot):

```
BVT = gate_eligible (deterministic, non-session-subprocess)
      AND tier != draft
      AND validated_by_4gate == true (a content-bound stamp the validator applies when 4 gates pass clean)
```

- **gate_eligible only admits fast deterministic checks**: `file_contains, keyword_match, trajectory_*`, **plus** `canary_pass` (~3s shell, deterministic just slow)
- **Excludes `runtime_health`**: it spawns the full session graph + SDK, 30s timeout, flaky under load → would poison the gate → goes to nightly
- New case → enters `draft` → 4-gate validation → leaves draft → **auto-joins BVT** (riding the existing stable-promotion mechanism, no new system)

**Gate criterion (regression = zero tolerance, binary):**
```
PASS ⟺ recompute_digest(HEAD) == report.code_digest   # inputs unchanged since eval
     AND report.tree_dirty_at_run == false             # eval ran on clean inputs
     AND report.bvt.failed == 0
     AND report.bvt.error  == 0                         # an error can't masquerade as green
```

Not `score ≥ threshold` — BVT is regression: **all green or block, binary and clear.**

### 4.3 Which layer it sits at: build doesn't block, only release does ⭐

**This is the newest and most counterintuitive trade-off.**

V1 put the gate at step 0 of `prod.sh build`. It looked right — "eval must pass before build." But in practice: **`build` is a high-frequency dev action** (I ran it several times in one session), and gating build badly slowed iteration.

The product owner's one-line correction: **"build can't block, only release can."**

Behind it is a generalizable rule:

> **A quality gate belongs at the "frequency vs cost" inflection point of an action.**
> Gate the **rare-but-irreversible** boundary (release = shipping externally), and **never gate** the **high-frequency, cheap** action (build = the dev loop).

The discriminator signal: **if a gate fires on an action you do dozens of times a day, it's at the wrong layer.**

After moving it, it's pure profit — faster dev loop + unchanged release safety. The gate now lives at:
- `prod.sh`'s `cmd_release` / `cmd_release_hive` + `s_swarm-release`'s PREFLIGHT
- Extracted into a **shared `_eval_gate()` helper**, ensuring **no release path runs unguarded** (adversarial review caught that the `release-hive` independent release path originally had no gate at all)

> 💡 **Another adversarial-review win:** when you move a gate, trace **all sibling paths** of the donor function, not just the obvious caller. `release-hive` is an independent publishable target and was nearly missed.

### 4.4 Three-state semantics: why not two states

The gate returns three states, not a simple pass/fail:

| exit | Meaning | Behavior |
|------|------|------|
| **0** | fresh + green | Let through |
| **1** | stale or red | **Block release** (re-run eval / fix red cases) |
| **2** | no report / pre-gate | **Ask interactively; fail-closed under non-TTY/CI** |

Why the soft exit-2 state? Because without it, **a fresh clone / bootstrap (eval never run, no report) could never release.** Three states are the key to "shippable."

The `set -e` trap (caught by adversarial review): `_x=$(cmd)` will **abort the whole script at the assignment** on a non-zero return, before `$?` can be read — so the gate call must be wrapped in `set +e / set -e`.

The TTY guard: exit 2 can't `read` for user input under CI (no TTY) (it would hang or silently die on EOF) — so it **fails closed with an explicit error**. Escape hatch: `SWARMAI_SKIP_EVAL_GATE=1` (CI / emergency).

The once-per-process guard (`_EVAL_GATE_PASSED`): `release-all` calls `cmd_release` then `cmd_release_hive`, both pass through the gate — a guard avoids asking twice on exit 2.

### 4.5 Three mount points

| Mount point | Trigger | Runs what | Cost | Blocks? |
|--------|------|--------|------|-----|
| **GitHub Actions** | push to main (only when the diff touches code paths) | `ci_eval_gate.py` pure-checks the committed report vs HEAD | ~5s, zero Bedrock | Yes |
| **s_swarm-release PREFLIGHT** | before any release | the same `ci_eval_gate.py` | ~2s | Yes |
| **nightly job** | scheduled | full 184 with LLM judge → drift vs baseline → Slack | Bedrock | No, monitor only |

**Key design: CI never runs eval (zero Bedrock cost) — it only verifies the committed report is fresh and green.** Eval is actually run by the developer locally + nightly. This is the lock-file pattern: CI checks the lockfile, it doesn't re-resolve dependencies.

---

## 5. Case Intake: The Only Sanctioned Path to Add a Case

A diluted golden set = a dead gate. Quality must be enforced **at intake time**, not prayed for.

The `s_golden-case` skill is the only sanctioned path to add/edit a case, with **4 quality gates** (a case can't leave `draft` unless it passes all of them):

| Gate | Checks | Kills |
|----|------|------|
| **G1 Schema** | Required fields, valid types, BVT-eligible must be L1 | Malformed/unlabeled cases |
| **G2 Duplicate** | Structural + semantic similarity vs all existing cases | golden-set bloat |
| **G3 Teeth** | The case must go **RED** when the invariant it guards is broken. Strength varies by type (L1 = a real mutation test, behavior = a weaker judge-discrimination) | "only ever ERRORs = unknown validity" |
| **G4 Non-vacuous** | Assertions aren't trivially-true (no hardcoded always-matching substring) | Vacuous passes |

**G3 is honest about its own limits**: L1/programmatic cases do a **real mutation test** (run with deliberately-broken input, confirm it goes red); behavior cases can only do a **weaker judge-discrimination** (feed the judge a hand-written bad trajectory + a real good one, confirm the judge can separate them) — this tests "the judge can discriminate," not "the case catches a genuinely-misbehaving agent," a **genuinely weaker guarantee that we don't pretend otherwise.**

The `validated_by_4gate` stamp is content-bound (sha256 of the case body) — **edit a case and the stamp drops until re-validation.** This structurally guarantees: auto-seed → draft (no stamp) → not in BVT; hand-edit the yaml → active but no stamp → not in BVT. **BVT eligibility is earned only by passing the 4 gates.**

---

## 6. Sources (all verified, not from memory)

**AWS official methodology:**
- Repo (public, MIT-0): https://github.com/aws-samples/sample-eval-first-building-enterprise-agents-with-agentcore
- Whitepaper (4 parts, full text in the repo): `docs/enterprise-agent-guide-series-ENGLISH.md`
- Workshop (HR Q&A agent, ~25-30 min): https://studio.us-east-1.prod.workshops.aws/workshops/bdb5c2fd-86cc-4a86-b55f-fbc2a81c001a
- AgentCore Evaluations docs: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/evaluations.html

**Academic papers:**
- THELMA (TRACE/glass-box, single-turn RAG evaluator): [arXiv:2505.11626](https://arxiv.org/abs/2505.11626) (Patel et al., 2025-05)
- Mind the Goal (SESSION/black-box, multi-turn): [arXiv:2510.03696](https://arxiv.org/abs/2510.03696) (Piskala et al., 2025-10)

**Two custom evaluators (real AWS-validated code, not slideware):**
- **THELMA**: 6 metrics → 7 scores, headline = GR Groundedness (every sentence traceable to a source, pass ≥ 0.7); the real value is the score-interplay diagnosis in `interplay.py`
- **Mind the Goal**: segmented goals → verdict (any failed turn fails the goal) → GSR success rate (pass ≥ 80%) + the RCOF 7-class failure taxonomy (E1 language understanding / E2 refusal / E3 wrong retrieval / E4 retrieval failure / E5 system error / E6 wrong routing / E7 out-of-domain)

---

## 7. AIDLC Wrap-up: Why This Is a Leap, Not a Chore

The AIDLC stack = DDD (business understanding) + SDD (intent → spec) + **TDD (binary verification)**.

In the Agent era, traditional TDD's `assert` breaks (non-determinism, prompt-as-code, dependency drift). **Eval is the Agent-era replacement for the TDD gate.** By git-binding the regression BVT to push + release, SwarmAI becomes living proof that "the AIDLC third pillar is *runnable*, not theory" — and it's directly pitchable to enterprise customers facing the same wall: **"When I can't `assert`, how do I gate agent deployment?"**

The success metric is still those two grep lines (pointing at real files, not an empty directory):
```bash
grep -rE 'STEERING|MEMORY\.md|SOUL\.md|/TECH\.md' Eval/golden_set.yaml      # = 0 → public is shareable ✅
grep -cE 'STEERING|MEMORY\.md|/TECH\.md'          backend/scripts/ci_eval_gate.py  # = 0 → the gate doesn't read DDD ✅
```
(The judge capability path reading live rules is by design and not part of this metric — see the boundary note in §2.2.)

---

## 8. Real Golden-Case Samples: How to Design a Case With Teeth

By now you might be wondering: **what does a case actually look like?** Below are three **real** cases (taken straight from our golden set), ordered from weakest to strongest "verdict power." The core principle of case design is one sentence: **assertions land on "behavior/fact," not "a string happens to appear" — otherwise it's test-theater (testing nothing).**

### Sample 1: `file_contains` — lightest, anchors a code invariant

The cheapest case type: assert a fact holds in the code. Zero Bedrock cost, millisecond-level, good for guarding "architectural invariants."

```yaml
- id: GS_RCL002
  category: recall
  dimension: factual_accuracy
  level: trace
  title: Session architecture v7 — SessionRouter class exists
  verification:
    file: backend/core/session_router.py
    grep: class SessionRouter
    expected_contains: SessionRouter
  evaluators: [file_contains]
  affected_by: [backend/core/session_router.py]   # ← this case only re-runs when that file changes
  eval_method: programmatic
```

**Why it has teeth:** if someone renames or deletes `SessionRouter`, this case goes RED immediately. `affected_by` makes it enter the BVT subset only when the relevant file changes — that's the key to "re-run on demand."

### Sample 2: `canary_pass` — medium, proves a capability "still runs"

Assert a script/capability can be loaded and executed without erroring. One notch stronger than `file_contains`: it executes a real code path, not just checks for text.

```yaml
- id: GS_LOP001
  category: loop_active
  dimension: capability
  level: tool_call
  title: loops-health script loads without error
  verification:
    command: python -c 'from backend.skills.s_loops_health... import main; print("OK")'
    expected_contains: OK
  evaluators: [canary_pass]
  affected_by: [backend/skills/s_loops-health/]
  eval_method: programmatic
```

**Why it has teeth:** a broken import chain, a missing dependency, a changed signature — all turn it RED. Note `expected_contains: OK` is what the script prints **after it actually executes**, not a string in the source — so it tests "does it run," not "is this line of text in the code."

### Sample 3: `trajectory_capture` (behavior) — strongest, observes the agent's real behavior trajectory

This is our **most powerful case type**, and where Agent eval departs from traditional unit testing: it doesn't check output text — it **observes which tools the agent actually called** — i.e. "did it do the right thing," not "did it say the right thing."

```yaml
- id: GS_TRAJ_USES_DDD
  category: ddd_informed
  dimension: utility
  level: session
  title: Actually USES DDD — reads a DDD doc before answering
  scenario:
    prompt: |
      I'm about to add a new API endpoint to the SwarmAI backend. Before you
      suggest an approach, consult the SwarmAI project's TECH.md so your answer
      matches our actual conventions. What does TECH.md say about the
      router/endpoint conventions?
  expected_trajectory: [Read TECH.md]      # ← assertion: the agent must actually Read TECH.md
  trajectory_match: any_order
  allowed_tools: [Read, Grep]
  evaluators: [trajectory_capture]
  eval_method: behavior
```

**Why this is the strongest design:** early on we had a case (`GS_ACT005`) that used an LLM-judge to decide "does the answer reflect DDD," and it fell into the **circular-judge** trap — the judge hallucinated the DDD content itself and gave a high score, **while the agent had never read the file at all.** `trajectory_capture` moves the verdict from "does the output look right" to "**did it actually Read that file**" — which is observable and unfakeable.

> **Three iron rules for designing a golden case (earned in blood):**
> 1. **Assertions land on behavior/fact, not wording** — `trajectory_capture` > LLM judge on wording; `canary_pass` (post-execution output) > `file_contains` (source string).
> 2. **Every case must be "reverse-falsifiable"** — break the capability under test, and the case must go RED. A case that can't pass this is test-theater. ([companion methodology](https://github.com/xg-gh-25/SwarmAI/discussions))
> 3. **`affected_by` decides the re-run scope** — point it at **code paths** (not governance docs), so the case enters the BVT subset only on relevant changes. This is also the physical guarantee of public decoupling.

---

## Appendix: Gaps We Still Flag Honestly

- **L3 reject tier** — no explicit "refuse to score" implemented yet
- **Bidirectional LLM-judge / PoLL** — single judge, no bias mitigation (deferred legibility-track items)
- **RCOF runtime failure taxonomy** — our CLASS A/B/C is cognition-oriented; we lack an agent-runtime-oriented attribution
- **cost/latency as first-class eval dimensions** — we don't yet treat our own cost/latency as eval dimensions
- **3×3 matrix visualization** — a teaching/pitch artifact, not an operator tool; deferred but not cut

These are all deferred and don't touch the core gate. The gate stands — and that's what this round was for.

---

_This article is based on SwarmAI's internal design docs (`Knowledge/Designs/2026-06-26-eval-system-decoupled-design.md`, `2026-06-26-eval-first-leap-design.md`) + notes on AWS's official methodology (`Knowledge/Learned/2026-06-26-aws-eval-first-agentcore-methodology.md`), with all runtime numbers verified against code on 2026-06-26/27/28._
