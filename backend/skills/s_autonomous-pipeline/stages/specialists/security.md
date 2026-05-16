# Security Specialist Review

Scope: When changeset touches auth, user input handling, database queries, file paths,
external API calls, or any endpoint/handler. NEVER_GATE — always dispatch regardless
of historical hit rate (insurance policy).

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
