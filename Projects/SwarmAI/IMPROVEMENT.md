
### 2026-05-16: Strangler Fig + Pipeline Self-Improvement Session

**What Worked:**
- **Thin delegates for zero-test migration** — 382 lines moved from hook → orchestrator, 0 test files changed. Delegate stubs (3 lines each) maintain backward compat. Commit f7824b12.
- **RP compound effect** — RP33 (multi-shape) caught today's CI bug class. RP34 (shell scope) caught F3 from PE review. Each RP prevents an entire class, not one instance.
- **Adversarial tier override (mechanical)** — diff > 100 lines = full tier regardless of profile. Would have caught today's F3 if it existed before the run.
- **Auto-discover > opt-in for gates** — Pollinate validator changed from `data.get("content_dir")` (opt-in, never fires) to auto-discover from `Knowledge/Pollinate/` (opt-out, always fires). Default path must trigger the gate.
- **Specialist ↔ REVIEW_PATTERNS sync** — Each specialist now references domain-specific RPs. Prevents the "two systems, different coverage" drift (LL12 recurrence).

**What Failed:**
- **First pollinate validator commit had 3 bugs** — sys.path pollution (F1), opt-in gate never fires (F2), indentation error (if outside try). All caught by same-session PE review. Root cause: "quick fix" mindset — wrote it fast, didn't trace the execution path mentally before committing.

**Anti-Patterns Encountered:**
- **Opt-in mechanical gate** — If the gate requires the actor to opt-in (add a field, pass a flag), it's not a gate — it's a suggestion. Real gates fire on the default path.
- **Specialist prompt drift** — Adding RP patterns to REVIEW_PATTERNS.md without syncing to specialist prompts = new knowledge that adversarial review can't use. Same root cause as LL12.
