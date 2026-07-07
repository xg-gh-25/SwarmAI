# Code Review

Structured, opinionated code review that produces actionable findings with file:line references. Zero external dependencies -- uses only git, gh, grep, and the agent's own analysis.

## Workflow

### Step 1: Determine Review Scope

From the user's request, determine what to review:

| Input | Action |
|-------|--------|
| PR number or URL | `gh pr diff {number}` to get the diff |
| Branch name | `git diff main...{branch}` |
| Specific files | Read those files directly |
| "my changes" | `git diff` (unstaged) + `git diff --cached` (staged) |
| Directory | Read all source files in that directory |

If ambiguous, ask: "What should I review? A PR number, branch, or specific files?"

### Step 2: Gather Context

Before reviewing, collect:

```bash
# For PRs: get description, comments, linked issues
gh pr view {number} --json title,body,comments,reviews,files

# For branches: understand the intent from commit messages
git log main..HEAD --oneline

# For any scope: check the project language/framework
# Look at package.json, Cargo.toml, go.mod, pyproject.toml, etc.
```

Read related files that the changed code depends on (imports, interfaces, types).

**Load the project's OWN standards — they OVERRIDE generic best-practice.** Before
applying any generic rule, check the repo for its declared conventions and review
against THOSE first:

```bash
# Project standards (read whichever exist)
for f in AGENTS.md AGENT.md CONTRIBUTING.md CONVENTIONS.md .editorconfig \
         docs/STYLE.md .github/pull_request_template.md; do
  [ -f "$f" ] && echo "== $f ==" && cat "$f"
done
```

Also note the configured lint/format rules (`.eslintrc*`, `pyproject.toml [tool.ruff]`,
`.golangci.yml`, `rustfmt.toml`) — these encode the team's accepted style.

**Rule:** a convention in the project's own standards beats a generic rule you'd
otherwise apply. When a finding rests on a project standard, **cite the standard**
(e.g. "violates CONTRIBUTING.md: handlers must return typed errors") so the author
can verify. When your generic instinct conflicts with a documented project rule,
the project rule wins — flag it as a QUESTION, not a CRITICAL.

### Step 2.5: Follow References (review against the REQUIREMENT, not just best-practice)

If the PR/branch/commit messages link an issue, ticket, or design doc, **fetch it and
review the code against what it PROMISED to do** — not only against generic quality.
A change can be clean code yet not satisfy its own requirement.

```bash
# Always works (local diff or PR): commit messages are the primary reference source
git log main..HEAD --format='%b' | grep -oE '#[0-9]+|https?://[^ )]+'

# ONLY when reviewing an actual PR (you have its number). Skip entirely for a
# local-diff / "my changes" review — there is no PR number to substitute.
gh pr view <PR_NUMBER> --json body,comments 2>/dev/null | grep -oE '#[0-9]+|https?://[^ )]+'
# Then, for each issue #NNN you found above, read it (repeat per number):
gh issue view <NNN> 2>/dev/null
```

If `gh` is unauthenticated or the scope is a local diff with no PR, rely on the
`git log` line alone — do NOT run the `gh` commands with a literal `{number}`
(they error, and the error is swallowed by `2>/dev/null` — you'd get silent empty
output, not a signal). Same fallback discipline as the Troubleshooting table.

For each reference, extract the acceptance criteria / stated goal, then verify the
diff actually delivers it. This becomes the **Requirements** review angle (Step 4).
If no references are found, state "Requirements: N/A — no linked issue/design" and
review against generic best-practice only.

### Step 3: Run Automated Checks

Run available linters/checkers based on project type (only if config exists -- never install anything):

```bash
# Check if linter configs exist, run only what's already set up
[ -f .eslintrc* ] && npx eslint --no-fix {files} 2>/dev/null
[ -f pyproject.toml ] && python3 -m ruff check {files} 2>/dev/null
[ -f .golangci.yml ] && golangci-lint run {files} 2>/dev/null
```

If no linters are configured, skip this step entirely -- rely on manual review.

**Don't restate the tooling.** If a linter/scanner already flagged something, do NOT
repeat it as your own finding — that adds no value (you'd just be a second linter).
Instead: (a) VERIFY the tool's finding is real (tools have false positives too), and
(b) find the **same-class issues the tool MISSED** — the ones that need human judgment
the linter can't do. Your value is what the automated check *couldn't* catch.

### Step 4: Manual Review

Review every changed line against these categories:

#### 0. Requirements (if references were found in Step 2.5)

| Check | What to Look For |
|-------|-----------------|
| Delivers the goal | Does the diff actually do what the linked issue/design asked? |
| Complete | Every stated acceptance criterion has corresponding code |
| No scope creep | Changes beyond the stated requirement — intentional or accidental? |

If Step 2.5 found no references, mark "Requirements: N/A" and skip to Correctness.


#### A. Correctness

| Check | What to Look For |
|-------|-----------------|
| Logic errors | Off-by-one, wrong operator, inverted condition, missing null check |
| Edge cases | Empty input, zero, negative, max values, concurrent access |
| Error handling | Unhandled errors, swallowed exceptions, missing try/catch |
| Resource leaks | Unclosed files/connections/streams, missing cleanup |
| Race conditions | Shared mutable state, missing locks, async ordering issues |
| Type safety | Wrong types, unsafe casts, any/unknown misuse |

#### B. Security

| Check | What to Look For |
|-------|-----------------|
| Injection | SQL injection, command injection, XSS, template injection |
| Auth/authz | Missing permission checks, privilege escalation paths |
| Secrets | Hardcoded keys, tokens, passwords, credentials in code |
| Input validation | Untrusted input used without sanitization |
| Dependencies | Known vulnerable patterns (eval, exec, innerHTML with user data) |
| Data exposure | Logging sensitive data, overly broad API responses |

#### C. Design & Architecture

| Check | What to Look For |
|-------|-----------------|
| Single Responsibility | Functions/classes doing too many things |
| Coupling | Tight coupling between modules, circular dependencies |
| Abstraction | Leaky abstractions, wrong abstraction level |
| API design | Confusing interfaces, inconsistent patterns |
| Extensibility | Hardcoded values that should be configurable |
| Duplication | Copy-pasted logic that should be extracted |

#### D. Readability & Maintainability

| Check | What to Look For |
|-------|-----------------|
| Naming | Unclear names, abbreviations, misleading names |
| Complexity | Deeply nested logic (>3 levels), long functions (>50 lines) |
| Comments | Missing "why" comments on non-obvious code, stale comments |
| Dead code | Unreachable code, commented-out blocks, unused imports |
| Consistency | Style inconsistencies with the rest of the codebase |
| Magic values | Unexplained numbers/strings that should be named constants |

#### E. Testing

| Check | What to Look For |
|-------|-----------------|
| Coverage gaps | New code paths without tests |
| Test quality | Tests that don't actually assert behavior, brittle tests |
| Edge case tests | Missing tests for error paths, boundary values |
| Test isolation | Tests depending on external state or ordering |

#### F. Performance (only flag when clearly problematic)

| Check | What to Look For |
|-------|-----------------|
| N+1 queries | Database queries in loops |
| Unbounded operations | Loading all records, no pagination |
| Memory | Large allocations in hot paths, growing without bounds |
| Unnecessary work | Redundant computation, extra network calls |

### Step 4.5: Self-Review (MANDATORY — before you write the report)

You are about to publish findings. Before you do, review your OWN findings once —
this is a pre-output evidence pass, distinct from the post-hoc Verification checklist
at the end. It catches the false positives and name errors that erode trust faster
than a missed bug does.

1. **Re-read the evidence** — for EACH finding, re-read the exact line(s) you cited.
   Confirm the problem exists in the actual diff, not in your memory or imagination.
2. **Verify every name** — every symbol, file path, function, class, config key, and
   URL you referenced actually exists and is spelled correctly (grep to confirm).
3. **Dedupe** — merge findings that describe the same root cause across files.
4. **False-positive sweep** — delete any finding you can't back with a concrete
   re-read. A suppressed maybe-bug is cheaper than a confident wrong finding.
5. **Severity calibration** — re-check each severity against the definitions below.

A single fabricated or misattributed finding makes the author distrust the whole
review. This pass is the cost of being trusted.

### Step 5: Assign Confidence & Generate Report

For every finding, assign a **confidence score (1-10)** using the Unified Confidence Rubric:

| Score | Meaning | Display Rule |
|-------|---------|-------------|
| 9-10 | Verified by reading specific code, concrete bug/exploit | Show normally |
| 7-8 | High confidence pattern match, very likely correct | Show normally |
| 5-6 | Moderate — could be false positive | Show with ⚠️ caveat |
| 3-4 | Low — suspicious but may be fine in context | **Suppress** from report |
| 1-2 | Speculation | **Suppress entirely** |

**Confidence modifiers:**
- +3: Constructed concrete failure/exploit scenario
- +2: Path reachable from user input
- +2: Similar bug was fixed before in this codebase
- -2: Internal-only endpoint or private module
- -4: Test/example/doc file
- Suppress entirely: Known false-positive patterns (placeholder, env var ref, version string)

**Multi-file confirmation:** If the same issue pattern appears in multiple files,
boost confidence +1 and tag "CONFIRMED across N files."

Record each finding with confidence:

```
{file}:{line} [{severity}] (confidence: N/10) {category} -- {description}
```

Severity:
- **CRITICAL** -- Bug, security issue, or data loss risk. Must fix before merge.
- **WARNING** -- Code smell, maintainability risk, or potential future bug. Should fix.
- **NIT** -- Style, naming, or minor improvement. Nice to have.
- **QUESTION** -- Not sure if intentional. Needs author clarification.

**Display gating:** Only include findings with confidence >= 5 in the main report.
Findings with confidence 3-4 go in an optional "Low Confidence (verify)" appendix.
Findings with confidence 1-2 are not shown at all.

Present the report:

```markdown
## Code Review: {scope description}

### Summary
- Reviewed: {N files}, {N lines changed}
- Findings: {N critical}, {N warnings}, {N nits}, {N questions}
- Verdict: {APPROVE / REQUEST CHANGES / NEEDS DISCUSSION}

### Critical
- `src/auth.py:42` [CRITICAL] Security -- User input passed directly to SQL query without parameterization. Use parameterized queries.
- ...

### Warnings
- `src/api/handler.go:88` [WARNING] Correctness -- Error from `db.Query()` is ignored. This will silently swallow database failures.
- ...

### Nits
- `src/utils.ts:15` [NIT] Readability -- `x` is unclear; consider `retryCount`.
- ...

### Questions
- `src/config.rs:30` [QUESTION] Design -- Is the 30s timeout intentional? Seems low for batch operations.
- ...

### What's Good
- {2-3 specific positive observations about the code}
```

**Always include "What's Good"** -- constructive review, not just a bug list.

### Step 6: Offer to Fix

For CRITICAL and WARNING findings:
- Show the exact code change needed
- Offer to apply fixes in batch
- For PR reviews, offer to post as a gh review comment

```bash
# Post review on GitHub PR
gh pr review {number} --comment --body "..."
# Or request changes
gh pr review {number} --request-changes --body "..."
```

---

## Review Styles

Adjust depth based on context:

| Context | Focus | Depth |
|---------|-------|-------|
| Quick check | Correctness + Security only | Skim |
| PR review | All categories | Thorough |
| Pre-merge audit | Security + Correctness + Tests | Deep |
| Refactoring review | Design + Readability | Thorough |
| Junior dev code | All + educational explanations | Mentoring |

If user says "quick review" or "just a glance", focus only on Critical/Warning items.

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Too many findings | Prioritize by severity; group related issues |
| Large PR (>500 lines) | Review in chunks by file; focus on non-test files first |
| No test changes in PR | Flag as WARNING: "No tests for new functionality" |
| Unfamiliar language | Focus on logic/design patterns; skip language-specific idioms |
| gh not authenticated | Fall back to `git diff` for local reviews |

## Escalation Protocol

### L0 INFORM (clean review)
```markdown
> [INFORM] **Review clean: 2 warnings, 0 critical** — no blockers found.
```

### L2 BLOCK (critical findings that need human judgment)
```markdown
> [BLOCK] **Critical finding needs human review**
>
> **Finding:** Hardcoded AWS credentials in config.py:42 (confidence 9/10)
> **Exploit:** Attacker with repo access gets production AWS access.
>
> This is auto-fixable but changes the deployment configuration.
> **Options:**
> 1. Auto-fix: move to env var (recommended)
> 2. Verify it's a test-only credential and suppress
> 3. Discuss the credential management approach first
```

Also BLOCK when review scope exceeds expectation:
```markdown
> [BLOCK] **Scope concern: changeset touches 47 files across 8 modules**
>
> A thorough review of this scope would take significant context budget.
> **Options:**
> 1. Review all 47 files (comprehensive but expensive)
> 2. Focus on the 12 files with logic changes (skip config/test)
> 3. Split into module-by-module reviews
```

## Anti-Rationalization

The agent will rationalize skipping steps. These rebuttals are non-negotiable:

| Agent Shortcut | Required Response |
|---|---|
| "The changes are too small to review thoroughly" | Every change gets the full checklist. Small changes hide big bugs. |
| "This is just a refactor, no functional changes" | Refactors are where regressions hide. Run the full correctness check. |
| "I'll skip security review — it's an internal module" | Internal modules get compromised through dependency chains. Check it. |
| "No tests changed so test coverage review isn't needed" | That's exactly when coverage gaps appear. Flag missing test updates. |
| "The author is senior, this is probably fine" | Code review is about the code, not the author. Same standards apply. |
| "Too many files to review thoroughly" | Escalate scope concern (L2 BLOCK). Never rubber-stamp a large changeset. |
| "I'll apply my generic style rules" | Load the project's OWN standards first (Step 2). A documented project convention overrides your generic instinct — flag conflicts as QUESTION, not CRITICAL. |
| "The linter already flagged these, I'll list them too" | Don't be a second linter. Verify the tool's findings, then find what it MISSED. Repeating automated output adds zero value. |
| "I'll just write the findings and ship" | Run the mandatory Self-Review pass (Step 4.5) first. Re-read every cited line + verify every name. One fabricated finding destroys trust in the whole review. |

## Verification

Before marking the review complete, show evidence for each:

- [ ] **Scope stated** — list files reviewed with line counts
- [ ] **All categories checked** — Correctness, Security, Design, Readability, Testing, Performance — state "no findings" explicitly per category if clean
- [ ] **Every finding has file:line** — no vague "the code has issues"
- [ ] **Severity assigned** — every finding is CRITICAL, WARNING, NIT, or QUESTION
- [ ] **"What's Good" included** — at least 2 specific positive observations
- [ ] **Verdict stated** — APPROVE, REQUEST CHANGES, or NEEDS DISCUSSION with rationale
- [ ] **Automated checks ran** — linter output shown or "no linter configured" stated

## Quality Rules

- Never rubber-stamp: always find at least one actionable insight
- Never pile on: group related findings, don't list every instance
- Be specific: "line 42 has X" not "the code has issues"
- Suggest, don't demand: "Consider..." not "You must..."
- Assume good intent: ask questions before assuming bugs
- Zero dependencies: uses only git, gh, and built-in tools
