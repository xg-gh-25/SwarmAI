# API Contract Specialist Review
<!-- version: 2026-07-21 | synced with: REVIEW_PATTERNS.md RP1-RP52 -->

Scope: When changeset touches router files, endpoint handlers, Pydantic models,
response schemas, or API-facing interfaces.

**Cross-reference:** Also check patterns from `REVIEW_PATTERNS.md` in your domain:
RP14 (cross-service parameter mismatch), RP24 (cross-language serialization format),
RP33 (multi-shape function returns — different consumers assume different shapes).

Output: JSON objects, one finding per line.

```json
{"severity":"HIGH|MED|LOW","confidence":N,"path":"file","line":N,"category":"api-contract","summary":"...","fix":"...","fingerprint":"path:line:api-contract","specialist":"api-contract"}
```

Required: severity, confidence, path, category, summary, specialist.
Optional: line, fix, fingerprint, evidence.
If no findings: output `NO FINDINGS` and nothing else.

---

## Confidence Scoring

Every finding MUST include confidence (1-10):
- 9-10: Verified by reading both producer and consumer code
- 7-8: Clear contract violation (type change, field removal, missing field)
- 5-6: Potential breaking change — depends on caller usage
- 3-4: Might affect some consumers, can't verify all callers
- 1-2: Speculative concern about future compatibility

Modifiers: +3 if found actual consumer that will break, +2 if public API
(external callers), -2 if internal-only (all callers in same codebase),
-3 if backwards-compatible addition (new optional field).

---

## Categories

### Breaking Changes
- Removed fields from response bodies (existing consumers depend on them)
- Changed field types (string → number, object → array, None → required)
- New required parameters added to existing endpoints
- Changed HTTP methods or status codes for existing endpoints
- Renamed fields without maintaining backwards compatibility
- Changed authentication requirements (unauthenticated → authenticated)

### Error Response Consistency
- New endpoints returning different error format than existing ones
- Error responses missing standard fields the frontend expects
- HTTP status codes that don't match error type (200 for errors, 500 for validation)
- Error messages that leak internal details (stack traces, SQL, file paths)
- Missing error handling that returns raw exception to caller

### Request/Response Schema
- Pydantic model changes that break serialization (camelCase ↔ snake_case)
- Missing field validation (accepting any type when specific type expected)
- Optional fields that should be required (or vice versa)
- Default values that mask bugs (None default when field is always needed)
- Inconsistent naming across endpoints (created_at vs createdAt vs timestamp)

### Frontend Contract
- Backend field rename without updating `toCamelCase()` in frontend service
- New backend fields missing from TypeScript interface definitions
- Changed response shape without updating TanStack Query hooks
- SSE event format changes without frontend parser update
- WebSocket message schema changes without frontend handler update

### Cross-Boundary Data Flow
- Data format assumptions that hold in producer but not consumer
- JSON serialization differences (date formats, None vs null vs missing)
- Encoding assumptions (UTF-8 not enforced at boundary)
- Numeric precision loss across serialization boundaries
- Array ordering assumptions (set/dict iteration order)

### Pagination & Limits
- List endpoints without pagination that will grow unbounded
- Changed page sizes or default limits without documentation
- Missing total count or next-page indicators
- Cursor-based pagination without stable sort order
