# Autonomous Pipeline Report: design-judgment Surface 6: attention follows the user's phase

**Run ID:** run_41ee8348 | **Project:** SwarmAI | **Profile:** docs
**Date:** 2026-10-17 17:32 UTC | **Duration:** 8.7 min (in progress) | **Tokens:** 0 (估算 / estimate)
**Gate 1:** N/A | **Files:** 1 | **Tests:** not recorded

## TL;DR
Sediment the agent-chat 'attention follows the user's phase' design philosophy into design-judgment.md Surface 6 (s_frontend-design): four user questions per turn (align / delegate / accept / retro), 

## 1. Requirement
Sediment the agent-chat 'attention follows the user's phase' design philosophy into design-judgment.md Surface 6 (s_frontend-design): four user questions per turn (align / delegate / accept / retro), visual weight follows the current phase not data volume, seven principles; and fix Surface 6 invariant (1) 'each action = a typed card' which conflicts with one-focus-at-a-time. No px/paths/run ids; whole-file contradiction check.

## 2. Evaluation
| Dimension | Score | Recommendation |
|---|---|---|
| Strategic | 4.00 | |
| Priority | 3.00 | |
| Feasibility | 5.00 | |

**Acceptance Criteria:**
1. AC1: Surface 6 states the four questions a user asks across one agent turn (align, delegate/progress, accept result, retro) and, for each, what gets visual weight and what recedes — readable on its own without SwarmAI context.
2. AC2: Surface 6 states the seven principles (one focus at a time; progress = position in plan not step count; summaries name effects not effort; findings attach to the work that produced them; one fact one representation; color only for exceptions needing action; process is folded not deleted, three-level disclosure), each with the user decision it serves.
3. AC3: The old invariant (1) 'each agent action = a typed card' no longer contradicts the one-focus principle: finished work folds into one summary line per segment, only the live segment is a bordered container; a reader cannot derive a card-wall from Surface 6.
4. AC4: Whole-file contradiction check: no other part of design-judgment.md (Surface 1 streaming invariant, Part 4 Depth, F4 tense-template status) contradicts the new text; any adjacent statement that does is reconciled in the same edit.
5. AC5: scripts/lint_skills.py exits 0 on the edited corpus (no run ids, register prefixes, or owner paths); no px values, file names of this codebase, or line numbers appear in the new text.
6. AC6: Existing anchors cited by code (Part N, Check N, Surface N numbering) are unchanged, so design_hooks references stay valid.


## 3. Design & Approach
**Research Findings (THINK):**
- Surface 6 is the only agent-turn surface in the canon; its invariant (1) prescribes one typed card per action, which renders as an equal-weight card wall once a turn has many segments.
- The genuinely new axis is TIME: the canon has strong static hierarchy rules (Check 4 Von Restorff, Part 4 Depth, Check 2 redundant ink) but nothing on how the single focus should MOVE as a turn progresses from intent to live work to result to retro.
- Two of the seven principles overlap existing checks: 'one focus at a time' is Check 4 applied across time, 'one fact one representation' is Check 2 applied to live status. They must be written as applications citing those checks, not restated rules.
- No code or other skill cites Surface 6 by invariant number (s_web-design-review and s_frontend-design INSTRUCTIONS only reference the file), and design_hooks cites only Part 2/3/5 anchors, so rewriting invariant (1) breaks no reference.
- Surface 6 invariant (2) 'live status per action (running/done/failed)' must stay compatible with 'color only for exceptions': status is still shown per step, only the done state goes neutral.

**Alternatives Evaluated:**
- SIMPLICITY — effort: S
- QUALITY — effort: S
- DELETION — effort: S

**Chosen Approach:** Rewrite only the Surface 6 block of backend/skills/s_frontend-design/data/design-judgment.md in place (QUALITY alternative): update convergence + why, add an 'Attention moves across the turn' bullet with the four questions, replace invariant (1), add a principles bullet with seven one-line principles cross-linked to Check 2/Check 4/Part 4, extend anti-patterns with the card wall and conflicting numbers, end with one tell sentence.

**Success Criteria:**
- lint_skills exits 0
- adversarial reviewer finds no HIGH/MED unresolved
- heading counts unchanged

**Always:**
- edit only the Surface 6 block
- keep numbering

**Never:**
- px values
- codebase file names
- ids
- editing the .claude projection


## 6. Decision Log
| Stage | Decision | Classification | Reasoning |
|-------|----------|---------------|-----------|
| evaluate | Profile = docs (single markdown file, no code) | mechanical | Decision tree q0: only .md changes |
| evaluate | Amend Surface 6 and rewrite invariant (1) rather than append | taste | Keeps one canonical statement; appending would lea |
| think | Write overlapping principles as applications of Check 2 and  | mechanical | Duplicating a rule in two places lets them drift;  |
| think | Choose QUALITY over SIMPLICITY | taste | A tell sentence and cross-links cost a few lines a |
| plan | Run one fresh-context adversarial review even though the doc | mechanical | AGENT R2 requires adversarial before every commit  |
| deliver | Ran adversarial review although docs profile tier skips spec | mechanical | R2: adversarial before every commit; it found 4 ME |
| deliver | Did not fix lint findings in deliver.md/review.md | mechanical | Those are another session's uncommitted edits (R29 |

**Summary:** 5 mechanical, 2 taste, 0 judgment

## 7. Quality Gates
### 7.5 Adversarial Review
**7 found · 7 fixed · 0 open**
- [MEDIUM] Surface 6 invariant (1): fold timing unspecified, auto-fold could reflow under t
- [MEDIUM] Status vocabulary mismatch (waiting / waiting-on-you / blocked) and no precedenc
- [MEDIUM] Convergence line overstated: 'Thought for Ns' is a reasoning fold and names dura
- [MEDIUM] Loophole: parallel sub-agents could justify one box per agent; raw-args rule cou
- [LOW] 'one step count' contradicted 'step counts belong behind disclosure'. Fixed: one

### 7.6 Completion Audit
**Requirements met:** 6/6
**All green:** yes


## 8. Files Changed
- `backend/skills/s_frontend-design/data/design-judgment.md`

## 9. Lessons
- [guideline] When writing an 'only X' rule into shipped design guidance, state its plural case and its exception in the same sentence (one live region shared by parallel agents; raw details hidden except in an approval request) — a cold adversarial read found both loopholes that the author's intent-driven read could not see.
- [principle] A new principle that overlaps an existing check belongs in the canon as an application of that check with a cross-reference, not as a restated rule — two copies of one rule in one reference file drift exactly as the file's own redundant-representation check warns.
- [pitfall] An example quoted to support a principle can itself break the principle: the folded-reasoning example named a duration while the new rule says summaries name effects, not effort. Check every example against the rule it illustrates before shipping.
- state the plural and exception of only-X rules
- cross-reference overlapping checks instead of restating


## 10. Known Gaps & Attention Flags
None identified.


---
*Generated by SwarmAI Autonomous Pipeline | 2026-10-17 17:32 UTC*
