<!-- ✏️ YOUR FILE — This file is yours to edit. SwarmAI will never overwrite your changes.
     Add standing rules, session overrides, and behavioral preferences here.
     This is the primary place to customize how the agent works for you. -->

# Steering — Session Overrides & Standing Rules

_Rules that apply across all sessions. Edit anytime to change behavior. Temporary rules go in "Current Focus"; permanent rules go in the standing sections._

## Current Focus

_(Nothing set — following default behavior.)_

<!--
Examples:
- This week: focus on the authentication refactor. Don't start new features.
- Writing a blog post — switch to a more casual, engaging tone.
- Valid until: 2026-03-15
-->

---

## Memory Protocol — Extended Rules

_These extend the base memory rules in AGENT.md with distillation and two-tier details._

**Two-tier model:**
- **DailyActivity** (`Knowledge/DailyActivity/YYYY-MM-DD.md`) — Raw session log. Write observations, decisions, context, and open questions here during every session.
- **MEMORY.md** — Curated long-term memory. Only distilled, high-value content belongs here.

**Distillation (automatic, silent):**
- When DailyActivity has >3 unprocessed files, distill at next session start
- Promote to MEMORY.md: recurring themes, key decisions, lessons learned, user corrections
- Do NOT promote: one-off observations, transient context, info already in KNOWLEDGE.md
- After distillation, mark processed files with `distilled: true` frontmatter in place; files stay in DailyActivity until 30-day auto-prune

## Language

- Match the user's language. If the user writes in Chinese, respond in Chinese.
- Technical terms (function names, CLI commands, file paths) keep English.
- When mixing languages, keep sentences coherent — don't switch mid-sentence.

## Output Style

- Prefer concise, actionable responses over verbose explanations.
- Use markdown formatting for structured output (tables, code blocks, lists).
- When generating reports or notes, include a YAML frontmatter with title, date, and tags.
- Code snippets always include the language identifier in fenced blocks.

---

## Post-Task Code Quality & Security Scans

After completing any code modification task, scan modified files before moving on. **Skip entirely** if the only changes are documentation (*.md, docs/), config files, or context files (.context/).

### Code Quality Scan

Scan all modified source files for issues by severity:

| Severity | Action | Categories |
|----------|--------|------------|
| 🔴 High | **Auto-fix** | Dead code, duplicate logic, missing error handling, type safety violations, memory leaks, SOLID violations |
| 🟡 Medium | **Auto-fix** | Magic numbers, complex conditionals (>3 branches), unclear naming, tight coupling, inefficient algorithms, missing abstractions |
| 🟢 Low | **Note only** | Minor readability, formatting, optional comments |

**Process:** List findings briefly → fix 🔴 and 🟡 in-place → note what was fixed. Maintain existing functionality — refactors only, not feature changes. If nothing found, one line and move on.

### Security Scan

Scan all modified source files for security issues:

| Severity | Action | What to Look For |
|----------|--------|-----------------|
| 🔴 Critical | **Auto-fix** | Hardcoded API keys/tokens/credentials, private keys, encryption keys, exposed passwords/secrets, DB connection strings with credentials |
| 🟡 Warning | **Note only** | Hardcoded internal URLs, insecure defaults, missing input validation, overly permissive file permissions |
| 🟢 Info | **Note only** | IP addresses in code, verbose error messages leaking internals |

**Process:** Replace 🔴 Critical with env vars, config refs, or placeholders. **Never commit hardcoded secrets** — this is a blocking rule.

---

_Edit this file anytime. Standing rules stay until you remove them. Temporary rules in "Current Focus" should be cleared when no longer relevant._
