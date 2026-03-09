---
name: Code Review
description: >
  Structured code review for PRs, files, or diffs with actionable findings.
  TRIGGER: "review code", "code review", "review PR", "review this file", "check code quality", "review my changes", "PR review".
  DO NOT USE: for UI/frontend-specific review (use web-design-review) or for auto-fixing code (use simplify).
  SIBLINGS: web-design-review = UI/accessibility audit | simplify = auto-fix quality issues | code-review = structured findings report.
version: "1.0.0"
---

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

### Step 3: Run Automated Checks

Run available linters/checkers based on project type (only if config exists -- never install anything):

```bash
# Check if linter configs exist, run only what's already set up
[ -f .eslintrc* ] && npx eslint --no-fix {files} 2>/dev/null
[ -f pyproject.toml ] && python3 -m ruff check {files} 2>/dev/null
[ -f .golangci.yml ] && golangci-lint run {files} 2>/dev/null
```

If no linters are configured, skip this step entirely -- rely on manual review.

### Step 4: Manual Review

Review every changed line against these categories:

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

### Step 5: Generate Report

For every finding, record with precise location:

```
{file}:{line} [{severity}] {category} -- {description}
```

Severity:
- **CRITICAL** -- Bug, security issue, or data loss risk. Must fix before merge.
- **WARNING** -- Code smell, maintainability risk, or potential future bug. Should fix.
- **NIT** -- Style, naming, or minor improvement. Nice to have.
- **QUESTION** -- Not sure if intentional. Needs author clarification.

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

## Quality Rules

- Never rubber-stamp: always find at least one actionable insight
- Never pile on: group related findings, don't list every instance
- Be specific: "line 42 has X" not "the code has issues"
- Suggest, don't demand: "Consider..." not "You must..."
- Assume good intent: ask questions before assuming bugs
- Zero dependencies: uses only git, gh, and built-in tools
