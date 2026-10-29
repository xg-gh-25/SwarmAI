---
name: learn-content
description: "Persist a useful external URL or document through CDF. TRIGGER: learn this URL, remember this article, save this reference."
tier: lazy
---
# Learn Content through CDF

Do not create or update files under `Knowledge/`. CDF is the only governed writer.

## User explicitly asked in this turn

Build one complete `ReferenceUnit`. Use `reference_kind: link` for a URL and include
`source_url`, a one-sentence standalone `summary`, and a complete `body` explaining
why the reference matters and when to use it. Submit with the current user event
(`last-user`: the CLI resolves this turn's message):

```bash
printf %s '{"kind":"reference","reference_kind":"link","scope":"world","title":"<title>","summary":"<one complete sentence>","body":"<why it matters and when to use it>","source_url":"https://example.com/path","evidence":[]}' | \
  "$SWARM_BACKEND_BIN" ingest submit --source S1 \
  --evidence last-user --payload-json - --wait 10
```

Relay the CLI echo to the user unchanged. S1 validates that the event is the latest
user message of this turn. A cron, auto-nudge, or injected turn must not call S1.

## Background or out-of-session fetch

Call `core.ingestion.sources.s6_external.extract_external` with the content already
fetched and its `web:` or `doc:` evidence anchor. S6 does not fetch the URL itself and
only emits Reference units. Content observed inside a session belongs to S2; content
observed inside a run belongs to S3.

## Boundaries

- Never write a Learned or Library file directly.
- Never truncate fetched content or a Reference semantic field.
- Do not save links that merely appeared; save references actually used or explicitly requested.
- Credential-bearing or non-HTTPS URLs are rejected by the server contract.
