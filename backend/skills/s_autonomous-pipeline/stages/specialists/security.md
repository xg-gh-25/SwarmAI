# Security Specialist Review
<!-- version: 2026-08-23 | synced with: REVIEW_PATTERNS.md RP1-RP80 (incl. Agent-Safety RP65-RP80) -->

Scope: When changeset touches auth, user input handling, database queries, file paths,
external API calls, or any endpoint/handler. NEVER_GATE — once dispatched (per
deliver.md scope rules), always runs to completion regardless of historical hit rate.
Dispatch decision is still scope-gated; NEVER_GATE means "don't skip based on stats."

**Cross-reference:** Also check patterns from `REVIEW_PATTERNS.md` in your domain:
RP17 (unsanitized strings in HTML/JSON/SQL), RP24 (cross-language serialization),
RP28 (schema migration without rollback), RP44 (identity-gate keyed by a raw name —
case/path/symlink aliasing), RP46 (enum-matched gate fails-open on an un-listed
value), RP49 (redaction written as a denylist — fails-open on fields added later;
invert to an allowlist-of-keep), RP52 (identity/principal read from the REQUEST
body/header/query — or a JWT decoded without signature verify — instead of the
server-verified principal; assert `attacker sets user_alias=<victim> in body →
expect 403`). These are proven vulnerability patterns. **If the changeset is an
LLM / agent system (see the Agent-Safety category below), ALSO apply RP65-RP80.**

Output: JSON objects, one finding per line.

```json
{"severity":"HIGH|MED|LOW","confidence":N,"path":"file","line":N,"category":"security","summary":"...","fix":"...","fingerprint":"path:line:security","specialist":"security","exploit":"attacker does X via Y to achieve Z"}
```

Required: severity, confidence, path, category, summary, specialist, exploit.
Optional: line, fix, fingerprint, evidence.
If no findings: output `NO FINDINGS` and nothing else.

---

## Confidence Scoring

Every finding MUST include confidence (1-10):
- 9-10: Constructed concrete exploit scenario with specific input
- 7-8: High confidence — known vulnerability pattern in reachable code
- 5-6: Moderate — pattern exists but exploit path unclear
- 3-4: Low — might be security-relevant, context insufficient
- 1-2: Speculation

Modifiers: +3 concrete exploit, +2 user-reachable path, +2 similar vuln fixed before
(IMPROVEMENT.md), -2 internal-only endpoint, -4 test/doc file.

**CRITICAL:** Every finding MUST include an `exploit` field describing the attack:
"Attacker does [action] via [vector] to achieve [impact]." Without a concrete
exploit scenario, the finding is speculation — rate confidence accordingly.

---

## Categories

### Injection Vectors
- SQL injection via string interpolation in queries
- Command injection via subprocess with user-controlled arguments
- Template injection (Jinja2, f-strings used as templates) with user input
- Path traversal via user-controlled file paths (../../etc/passwd)
- SSRF via user-controlled URLs (fetch, redirect, webhook targets)
- Header injection via user-controlled values in HTTP headers
- JSON injection via unescaped user strings in JSON construction

### Auth & Authorization Bypass
- Endpoints missing authentication middleware
- Authorization checks that default to "allow" instead of "deny"
- Role escalation (user can modify own role/permissions)
- Direct object reference (user A accesses user B's data by ID swap)
- Session fixation or token reuse opportunities
- API key validation that doesn't check expiration/revocation

### Input Validation at Trust Boundaries
- User input accepted without validation at handler/endpoint level
- Request body fields used without type checking or schema validation
- File uploads without type/size/content validation
- Webhook payloads processed without signature verification
- Query parameters used directly in database or file operations

### Secrets Exposure
- API keys, tokens, or passwords in source code (even comments)
- Secrets logged in application logs or error messages
- Credentials in URLs (query params, basic auth in URL)
- Sensitive data in error responses returned to users
- PII stored in plaintext when encryption is expected
- Secrets readable from environment without restricted access

### XSS & Output Encoding
- `dangerouslySetInnerHTML` / `v-html` / `.html_safe` with user content
- `innerHTML` assignment with unsanitized data
- Template literals building HTML from user input
- Missing Content-Type headers allowing browser content sniffing
- SVG/XML injection via user-controlled data in structured formats

### Cryptographic Misuse
- Weak hashing (MD5, SHA1) for security-sensitive operations
- Predictable randomness (Math.random, random.random) for tokens
- Non-constant-time comparisons on secrets (== instead of hmac.compare_digest)
- Hardcoded encryption keys or initialization vectors

### Deserialization & Data Trust
- Deserializing untrusted data (pickle, yaml.load, eval)
- JSON.parse of user input without schema validation
- Trusting data from external APIs without verification
- Accepting serialized objects that can trigger code execution

### Agent-Safety (LLM / agent systems) — RP65-RP80

**Apply this whole category ONLY when the changeset builds or modifies an LLM / agent
system:** it assembles a model prompt from runtime-fetched content, registers/dispatches
tools or MCP servers the model can call, spawns or delegates to sub-agents, persists agent
memory/context, grants the agent credentials or side-effecting actions, or loads agent
"skills"/plugins. These classes are invisible to per-file scanning — the vuln is in the
**composition / trust-flow / provenance**, not any single well-formed line.

**Unifying principle (state it in every finding here):** defense must be a DETERMINISTIC
boundary in code — an allowlist, a scoped credential, a verified principal, an egress jail,
a provenance check — NEVER a prompt instruction asking the model to behave. Assume prompt
injection SUCCEEDS; the dangerous action must be *ungrantable*, not merely *discouraged*. A
"please ignore injections" instruction and a client-side "trust" toggle are theater.

- **RP65 — prompt injection into the instruction channel:** runtime-ingested content (web
  page, RSS, chat, file, RAG doc, tool/MCP result) merged into the prompt with system-level
  authority. Verify it enters ONLY as delimited data, AND a downstream deterministic gate
  makes a successful injection unable to act.
- **RP66 — tool-composition gadget chain:** an agent registered with ≥2 of WRITE / EXECUTE /
  EXTERNAL-FETCH capabilities. The tool SET is the vuln (no single tool is buggy). Verify a
  registry classifies capability and refuses a second dangerous one; enumerate the whole
  agent's tools, not each alone.
- **RP67 — access policy in a doc-comment / prose the model is told to obey:** authz rule
  stated as advisory text, not enforced in code. Verify identity/authz is derived from the
  verified principal in the code path; the field is removed from the input schema.
- **RP68 — model output fed to an interpreter (text-to-code / NL2SQL):** `model.complete()`
  → `exec`/`eval`/query-engine, guarded by a denylist. Verify the "emit then run" step is
  REMOVED (templated, allow-listed, typed-param queries by ID); a denylist is a stop-gap.
- **RP69 — agent-action isolation:** side-effecting actions run with a broad standing role,
  so one task reaches another's blast radius. Verify the runtime role is task-scoped (no
  wildcards on powerful services); irreversible actions gated in code.
- **RP70 — persistent-state / cross-agent poisoning:** memory / shared store / RAG index
  written by one run and read as trusted instruction by a later run or sibling. Verify
  persisted state is untrusted on read (data, not instruction), every write attributed,
  stores isolated per task/tenant.
- **RP71 — standing broad credentials vs per-task least-privilege:** a fat static key / an
  always-on admin session. Verify credentials are minted per task, scoped, short-TTL,
  expired after; no personal/admin identity reused for agent tasks.
- **RP72 — caller self-issues a user-identity token instead of the owner holding a grant:**
  for unattended on-behalf-of actions the caller mints a signed `sub=user` token. Verify
  authority is inverted — the resource owner persists a revocable GRANT, the caller carries
  only a reference. A service minting identity + running JWKS IS an anti-pattern identity
  provider, however well the token is hardened.
- **RP73 — async entrypoint bypasses API-layer authz:** the same state-changing action is
  reachable via a queue consumer / storage event / job that skips the handler's checks.
  Verify authz+validation is ONE shared path both entrypoints call; every message untrusted
  regardless of transport.
- **RP74 — SSRF via an LLM-callable tool:** a tool fetches a model/request-supplied URL with
  no egress restriction. Verify a deterministic outbound allowlist enforced at fetch time on
  the RESOLVED IP (defeats DNS-rebinding TOCTOU), metadata endpoint + private ranges blocked,
  fetching tools in an egress jail.
- **RP75 — sub-agent delegation params trusted from the model:** the orchestrator passes
  model-chosen `customerId`/`tenantId`/`principal`/scope to a sub-agent. Verify identity/scope
  args are OVERRIDDEN with verified session values after the model proposes the call — the
  model picks intent, never the authority args.
- **RP76 — AI agent as confused deputy:** the agent holds the human's full admin creds and can
  issue a destructive prod call the task never required; a client "trust-all" flag is the only
  gate. Verify THREE deterministic layers (read-only session creds; service-side scope check;
  out-of-band confirm on mutating calls) — a client-side control alone is theater.
- **RP77 — hardcoded credential in source / commit history:** a live secret in code, config,
  or git history. Verify it is ROTATED (compromised the moment committed — deletion ≠ rotation),
  migrated to a secret store; check binary files too.
- **RP78 — agent skill loaded without provenance verification:** a skill/plugin loaded from a
  registry with no signature/provenance check — its body IS attacker-delivered code and may
  write the agent's own governance/memory files (session-persistent backdoor). Verify signing +
  provenance BEFORE load, a behavioral scan, and that skill code can NEVER write governance/memory.
- **RP79 — skill supply-chain: transitive trust or post-approval drift:** a dependency skill
  trusted transitively, or an approved skill silently updated (auto-update / mutable tag) to a
  different body. Verify pinning by immutable CONTENT HASH (not a tag), closure-level
  verification, and hash re-check at every load (fail-closed on drift).
- **RP80 — cross-platform skill reuse assumes identical security semantics:** one skill reused
  across agent platforms whose sandbox/permission/parse models differ (a config inert on A is an
  exec trigger on B). Verify per-target-platform validation before deploy; never assume
  portability of security semantics.
