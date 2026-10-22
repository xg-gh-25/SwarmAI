# REVIEW ref — Pipeline checks: tiers + core checks (0–4, 6)
Read when: running card step 4 (single-pass, or the main-context checks alongside
fan-out). The surface checks (5, 7, 7b, 16) are in `checks-surface.md`; the system checks
(8–13, 17) in `checks-system.md`; 14–15 in `exit.md`.

## Tiers (17 pipeline-specific checks on top of the base methodology)
**Tier 1 — always:** 0 Code Intelligence Context (if the DB exists) · 1 Code Review vs
TECH.md · 2 Security Scan · 3 Integration Trace · 6 Runtime Pattern Checklist (RP1-RP91,
incl. RP35 pool contention, RP36 fix-enables-regression, RP37 process tree lifetime) ·
14 Anti-Rationalization Gate · 15 Exit Evidence Checklist · 17 Blocking Constraints
(passes silently when TECH.md has no such section).

**Tier 2 — conditional:** 4 Replace/Move Parity (code moved/replaced) · 5 UX Review
(frontend files) · 7 Cross-Boundary Wire Test (frontend + backend) · 7b Operational
Context (operational code) · 8 Depth & Seam (new files) · 9 Blast Radius
(infra/release/deploy/CI) · 10 Operational Patterns OP1-OP8 (lifecycle ops on
infra/cloud subsystems) · 11 Inverse Operation (new state transitions) · 12 Cross-File
Consistency (file has known siblings) · 13 Neighborhood Review (modified functions in
files with >5 functions) · 16 Platform Matrix (path resolution, env vars, filesystem
ops).

## 0. Code Intelligence Context (only when `Projects/<P>/code_intel.db` exists)
Run before all other checks. Do not run `python -c`; reason from the changeset:
1. **Freshness** — DB present? At session start `context_health_hook` already ran an
   incremental refresh.
2. **Blast radius** — per changed file, `find_callers` / `find_dependents` logic: which
   files import or call its symbols? List them.
3. **Risk** — modules crossed; changed functions with high caller counts; changed code
   with no test-file callers.
4. **Inject** into the review preamble, e.g.
   `Risk Map: HIGH | 3 functions changed | 14 downstream | 2 untested` /
   `Modules crossed: core → hooks → channels (3-way)` / `Top concern: <fn> has 8 callers, 0 tests for error path`.
Risk HIGH → expand full blast-radius details before the checks. Risk CRITICAL → WARN
"This changeset has CRITICAL risk. Consider splitting." Feeds check 3 (caller list),
check 6 (RP25 blast radius computed, not manual), check 8 (module_map seam count).
Artifact: `"code_intel": {"blast_radius_computed": true, "risk_score": 0.45,
"risk_mitigated": true, "untested_callers": 2, "modules_crossed": 3,
"cross_module_justified": true}` — feeds confidence_score.py (+1 blast computed, -2 high
risk, -2 untested, -1 cross-module). Skip when no DB exists.

## 1. Code Review vs TECH.md
Review the changeset against the project's TECH.md conventions.

## 2. Security Scan (confidence-gated) — runs every time, even when no security sub-agent
- Each finding needs confidence (1-10) + an exploit scenario.
- ≥ 8 + Critical/High: auto-fix (mechanical decision). 5-7: warning only (taste
  decision). < 5: suppress. Apply the 10 false-positive exclusions.
- Check IMPROVEMENT.md for known issue patterns.
- Suppressing is valid for low confidence, but suppressing ALL findings means none was
  tried: verify at least the top 3 by evidence.

## 3. Integration Trace `[GATE·validator]` <!-- R-review-005 -->
`integration_trace` is a REQUIRED review-artifact field (validator BLOCKS if absent). The
trace CONTENT is agent-produced; only its presence is code-checked. Verify every new
public symbol is actually wired: for every new function, parameter, config key or
`.get("key")` call, grep for production callers (exclude test files).
| New symbol type | Verification | Example |
|-----------------|-------------|---------|
| New public function | ≥ 1 non-test caller | `generate_memory_index()` called by `inject_index_into_memory()` |
| New parameter on existing function | ≥ 1 call site passes it | `memory_progressive=True` passed by `prompt_builder.py` |
| New config key in DEFAULT_CONFIG | Trace `DEFAULT_CONFIG` → `config_manager.get()` → consumer | `memory_progressive_disclosure` read by prompt_builder |
| `agent_config.get("key")` / `config.get("key")` | The key has a setter | `_first_user_message` — no setter |
| New CLI flag / argument | ≥ 1 caller passes it | `--regenerate-index` — 0 callers |
| **Calling convention mismatch** | async callee from a sync caller has an explicit bridge (`asyncio.run()`, `get_running_loop().create_task()` with loop guard) | sync `bedrock.invoke()` → `async record_token_usage()` via bare `create_task`: no running loop in job context, task silently lost |
**On findings:** 0 production callers → **WARN** (not BLOCK). Either wire it now (add the
caller) or document it as intentional ("deferred — caller planned for Phase X").
Undocumented dead symbols are not acceptable: every WARN needs a resolution. (WARN, not
BLOCK, because some interfaces are designed ahead of their callers.) Record under
`"integration_trace"` (`{checked, clean, details}`; validator Check 8 semantic depth
WARNs when `checked` is 0). Output line: `N symbols checked, M connected, K warnings`.

### 3b. Input Boundary Analysis (modified functions whose behavior changed, not refactors)
For each such function, trace all callers and find the **most extreme valid input**:
1. `grep -rn "function_name(" --include="*.py" | grep -v "def \|test_"` → callers.
2. Per caller: which values does it pass? Minimum / maximum?
3. Run the function (mentally or literally) with the extreme input. Is the result right
   for the use case?
| Finding | Action |
|---------|--------|
| Extreme-but-valid input causes an unexpected tier/category jump | Add a guard or document as intentional |
| Caller hardcodes a value that bypasses the intended logic | Flag as design tension |
(Worked example — thin evidence promoted to a HIGH tier — in `stages/history/review.md`.)

## 4. Replace/Move Parity (code moved or replaced, not just added)
| Check | What to verify | Example |
|-------|---------------|---------|
| Feature parity | Every capability of the old code exists in the new | Old `_recall_knowledge` had TranscriptStore; new `_recall_for_query` must too |
| Dead orphan detection | After removing a call site, grep the old function — 0 callers left → flag dead code | `_recall_knowledge` still defined after its only caller was removed |
| Argument validity | Mock attributes must exist on the real class | `unit.working_directory` doesn't exist on SessionUnit |
| **Control-flow preservation** | Moved code executes at the same point in the caller's flow — check early returns, guards and branches ABOVE the new call site (`if X: return` before line N ⇒ code after N never runs when X) | `_run_data_cleanups()` extracted but placed after a fast-path `return` — never ran for up-to-date DBs |
| **Duplicate detection** | After adding a method, `grep -n "def method_name"` in the same file — a parallel session may have added a stub | Two definitions; Python silently uses the last one |

## 6. Runtime Pattern Checklist `[GATE·validator]` <!-- R-review-006 -->
`runtime_patterns` is a REQUIRED review-artifact field (validator BLOCKS if absent). The
RP1-RP91 range matches REVIEW_PATTERNS.md's current max (RP91). The rows to apply come
from `spawn_prompt.py --patterns-for` (Pipeline v1: the engine selects them into the correctness handout, `core/run_protocol/review.py` `review_patterns`), which
lists them compactly; give each listed row pass / N/A / fail; `spawn_prompt.py --patterns-rows RP<n> …` prints the full row (what to
verify, example bug) for any row whose trigger is present in the change.
For each emitted pattern that applies, explicitly verify the fix is in place. Do NOT skip
patterns: a "no" / N/A answer is fine, silence means unchecked. Record under
`"runtime_patterns"`: `{"checked": N, "violations": 0, "patterns": [{"pattern": "RPnn
name", "status": "pass", "detail": "what was checked (>10 chars)"}]}` (status ∈ pass /
N/A / fail).
`--patterns-for` matches each row's `match:` (glob / kw / `*` = always). Passing
`--diff-file` lets diff CONTENT trigger `kw:` terms too, so pass it whenever the diff is
available; a path-only run can miss content-triggered rows.
