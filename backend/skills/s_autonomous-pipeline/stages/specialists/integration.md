# Integration Specialist Review
<!-- version: 2026-05-19 | origin: PE Finding #1 (governance gate dead code) -->

Scope: Dispatched when changeset creates new public functions or modules.

**Purpose:** Verify that new code is WIRED into the system — not just correct
in isolation. PE reviews repeatedly find "correct but dead" code: functions that
pass all tests but have zero callers in production.

This is the symmetric check to correctness: correctness asks "is this code right?"
Integration asks "does anyone call this code?"

Output: JSON objects, one finding per line.

```json
{"severity":"HIGH|MED|LOW","confidence":N,"path":"file","line":N,"category":"integration","summary":"...","fix":"...","fingerprint":"path:line:integration","specialist":"integration"}
```

Required: severity, confidence, path, category, summary, specialist.
Optional: line, fix, fingerprint, evidence.
If no findings: output `NO FINDINGS` and nothing else.

---

## Confidence Scoring

Every finding MUST include confidence (1-10):
- 9-10: Grep'd codebase, confirmed 0 callers, traced full call chain
- 7-8: Function exists, likely no caller based on module structure
- 5-6: Possible dead code but may have dynamic/deferred caller
- 3-4: Might be called via string dispatch or plugin system
- 1-2: Speculation

---

## Checklist

### 1. Caller Existence (for each NEW public function/class)

For each new public function or class in the changeset:

```bash
grep -rn "function_name" <project_root> --include="*.py" | grep -v "def function_name" | grep -v "test_"
```

- **0 callers (excluding tests):** HIGH — dead code. Function exists, passes tests, but nothing in production calls it.
- **1+ callers:** Verify the caller's context matches the function's assumptions (see #3).

### 2. Registration/Wiring (for new hooks, handlers, adapters)

For code that must be REGISTERED to be active (hooks, middleware, adapters, CLI commands):

- Is there a registration call in the startup/init path?
- Does the registration file import the new symbol?
- Is the registration conditional (feature flag, platform guard)? If so, is the condition met in the target environment?

**Common patterns to check:**
- Hook created but not added to `hook_builder.py`
- Router defined but not mounted in `main.py`
- Adapter written but not registered in `gateway.py`
- Skill SKILL.md written but not in skills/ directory tree
- Config option added but never read by any consumer

### 3. Data Flow Verification (caller → callee contract)

For each caller of a new function:

- Does the caller pass the correct argument types?
- Does the caller handle the return type correctly?
- If the function has side effects (writes files, emits events), does the caller expect them?
- If the function reads state (files, DB, env vars), does that state exist in the caller's context?

**Proven failure pattern:** Function designed for context A (dev, full env), called from context B (daemon, stripped env). Tests pass in A, crash in B.

### 4. Import Chain Completeness

- New module imported by its consumer?
- Import at top level (always loaded) or deferred (loaded on demand)? Match the usage pattern.
- Circular import risk? (New module imports from a package that imports from it)

### 5. Configuration/Schema Wiring

For new config options, schema fields, or feature flags:

- Is the config option READ anywhere? (write without read = dead config)
- Is the schema field PRODUCED by any code path? (consumer without producer = always-empty)
- Is the feature flag CHECKED in the code path it's supposed to gate?

---

## What This Specialist Does NOT Check

- Code correctness (→ correctness specialist)
- Security implications (→ security specialist)
- Performance of the call chain (→ performance specialist)
- API contract with frontend (→ api-contract specialist)

This specialist ONLY checks: "Is the code CONNECTED to the system?"
