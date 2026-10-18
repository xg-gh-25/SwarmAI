# Autonomous Pipeline Report: REVIEW lists applicable review patterns compactly; full rows on demand

**Run ID:** run_a8fe815b | **Project:** SwarmAI | **Profile:** full
**Date:** 2026-10-18 15:50 UTC | **Duration:** 28.5 min (in progress) | **Tokens:** 1,183,014 (measured per response, cache read 41,280,544)
**Gate 1:** WARN | **Files:** 8 | **Tests:** 61 run, 0 failed

## TL;DR
**解决了什么问题**
- 原来：spawn_prompt.py --patterns-for printed every applicable REVIEW_PATTERNS row in full, so a REVIEW read 35-70K chars (32-49 rows) while recorded verdicts covered 3-14 patterns; the code-quality expert was told to read the whole 133K-char file.
- 现在：--patterns-for lists the same rows as one compact line each (id, pattern, trigger): 54,167 -> 9,800, 36,479 -> 6,965, 34,708 -> 8,343 chars on the measured change sets. --patterns-rows prints the full rows of any pattern whose trigger is present. The code-quality expert uses the same two commands. plan.md and review.md are back under the 5,000-char card limit.

**怎么做的**
- render_patterns_compact over the same _select as the full renderer
- --patterns-rows with id validation and --patterns-source
- review.md, checks-core.md, exit.md, REVIEW_PATTERNS.md and code-quality.md state verdict-every-row and fetch-before-judge
- plan.md replan note moved to ref/plan/exit-contract.md to fit the card limit

**遗留问题**（6 条，见 §10）

## 1. Requirement
Item 6 of the pipeline speed-up audit: the REVIEW_PATTERNS.md rows emitted at REVIEW (spawn_prompt.py --patterns-for) are still the largest remaining pipeline-doc load (~80K chars measured earlier). Cut what the agent must read at REVIEW without dropping any pattern that applies to the changed files: measure the emitted size and the per-row hit relevance on recent real runs first, then shrink (e.g. tighter applicability matching, a compact one-line form with the full row on demand). Every applicable RP must still get a verdict.

## 需求溯源审计 (Requirement Provenance Audit)

### 原始意图 (Original Intent)
> 起 pipeline：第 6 项精简评审规则表

### 需求推导 (Derivation)
## Requirements

| ID | work_class | source | status | AC present | Acceptance (GWT) |
|----|-----------|--------|--------|:---------:|------------------|
| FR-1 | improvement | user-stated: XG: 起 pipeline：第 6 项精简评审规则表 | confirmed | ● | Given a change set, when spawn_prompt.py --patterns-for <files>, then the same rows are listed, one compact line each, at most 40% of the old size |
| FR-2 | improvement | derived: every applicable RP must still get a verdict; judging a hit needs 'What to verify' | confirmed | ● | Given RP7 RP13, when spawn_prompt.py --patterns-rows RP7 RP13, then their full rows are printed |

### 澄清问答 (Clarifications — who decided what)
_(无澄清记录 / no clarifications)_

### ⚠️ 遗留 / 未闭合 (Leftover — what the AI decided for you)
✅ 无未闭合问题 (no leftover — all requirements closed, no AI assumptions)

## 2. Evaluation
| Dimension | Score | Recommendation |
|---|---|---|
| Strategic | 6.00 | |
| Priority | 6.00 | |
| Feasibility | 9.00 | |

**Acceptance Criteria:**
1. AC1: spawn_prompt.py --patterns-for emits every applicable row (same selection as today) as one compact line: id, pattern name, trigger text without its `match:` token; no 'What to verify' or 'Example bug' text
2. AC2: a new --patterns-rows RP<n> [RP<m> ...] prints the full table rows (header + rows) for the named ids; an unknown id exits 1 naming it
3. AC3: on the three measured change sets the --patterns-for output is at most 40% of today's size (54,167 / 36,479 / 34,708 chars)
4. AC4: review.md step 4, ref/review/checks-core.md and ref/review/exit.md say: verdict every listed row; fetch the full row with --patterns-rows before judging any row whose trigger is present


## 3. Design & Approach
**Research Findings (THINK):**
- render_patterns_for (spawn_prompt.py 199-215) prints header + each applying row whole; parse_review_patterns already splits rows and the match token
- Compact 3-column table (id | pattern | trigger without match token) measured at 18-23% of today's output on the three change sets (9,386 / 6,569 / 7,939 vs 53,523 / 35,963 / 34,210 chars)
- Dropping only the Example column keeps 66-73%: not enough
- test_spawn_prompt_patterns.py asserts '| RP2 |', a '| # |' header and ids from split('|')[1] — a 3-column table keeps those true

**Alternatives Evaluated:**
- SIMPLICITY — effort: S (1 session)
- DELETION — effort: S
- QUALITY — effort: M

**Chosen Approach:** Add render_patterns_compact (header + 3-column rows) as the --patterns-for output and render_patterns_rows for --patterns-rows; keep render_patterns_for (full) for --patterns-rows reuse; update the four docs.

**Success Criteria:**
- tests pass
- output <= 40%

**Always:**
- selection unchanged

**Never:**
- change any RP row


## 6. Decision Log
| Stage | Decision | Classification | Reasoning |
|-------|----------|---------------|-----------|
| evaluate | Keep row selection identical, shrink the rendering | judgment | selection is fail-toward-inclusion by design; the  |
| evaluate | Defer the one-verdict-per-printed-row validator check | taste | needs a run-scoped record of what was printed; sep |
| think | 3-column compact table, not a free-text list | taste | keeps the existing table assertions and stays scan |
| plan | Also point the code-quality expert at the compact listing | judgment | its checklist told the sub-agent to read the whole |
| build | Fixed the plan.md card size regression from the previous run | mechanical | lint_skills size ratchet failed on HEAD because of |
| build | Bare scripts/spawn_prompt.py path in review.md step 4 | taste | keeps the card under 5,000 chars; step 3 already u |
| deliver | Leave rules.json untouched | judgment | it is the frozen preservation manifest checked by  |

**Summary:** 1 mechanical, 3 taste, 3 judgment

## 7. Quality Gates
### 7.5 Adversarial Review
**3 found · 2 fixed · 1 open**
- [LOW] backend/skills/s_autonomous-pipeline/scripts/spawn_prompt.py the compact output'
- [LOW] backend/experts/code/code-quality.md forbade the full-file read with no fallback
- [LOW] backend/skills/s_autonomous-pipeline/stages/rules.json R-review-007 text still s

### 7.6 Completion Audit
**Requirements met:** 4/4
**All green:** yes


## 8. Files Changed
- `backend/skills/s_autonomous-pipeline/scripts/spawn_prompt.py`
- `backend/tests/test_spawn_prompt_patterns.py`
- `backend/skills/s_autonomous-pipeline/stages/review.md`
- `backend/skills/s_autonomous-pipeline/stages/ref/review/checks-core.md`
- `backend/skills/s_autonomous-pipeline/stages/ref/review/exit.md`
- `backend/skills/s_autonomous-pipeline/REVIEW_PATTERNS.md`
- `backend/experts/code/code-quality.md`
- `backend/skills/s_autonomous-pipeline/stages/plan.md`
- `backend/skills/s_autonomous-pipeline/stages/ref/plan/exit-contract.md`

## 9. Lessons
- [pitfall] A review checklist emitted in full is read in full: 32-49 rows and 35-70K chars per REVIEW while 3-14 verdicts were recorded. List the applicable rows compactly and fetch the full text only for rows whose trigger is present; keep the selection itself fail-toward-inclusion.
- [pitfall] When trimming a doc to fit a size limit, a shortened command path is a silent regression: it fails from the real cwd and pushes the agent back to the expensive fallback. Pin every runnable command a doc or tool output prints with a test that the path exists from the repo root.
- [pitfall] A change that adds words to a size-limited card can push it over a lint ratchet that the run's own regression set never runs; include the skills/doc linters in the verification set whenever a stage card changes.


## 10. Known Gaps & Attention Flags
- 未修复的 LOW 发现：backend/skills/s_autonomous-pipeline/stages/rules.json R-review-007 text still says read the whole file (RP1-RP88); it is the frozen rule-preservation manifest, not read at runtime; left unchanged
- Follow-up: validator check that every printed RP row gets a verdict and that triggered rows had --patterns-rows fetched (fetch-before-judge is doc-only today)
- The SwarmWS projection of the skill updates on the next sync/daemon restart
- This run also brought plan.md back under the 5,000-char card limit; the previous run had pushed it over
- 风险：LOW deployment: code-quality expert had no fallback -> fixed
- 风险：LOW first-run: the SwarmWS skill projection keeps old card wording until the next sync; the compact output's closing hint names --patterns-rows, so it explains itself


---
*Generated by SwarmAI Autonomous Pipeline | 2026-10-18 15:50 UTC*
