# 修复上下文溢出（API Error 400 "Input is too long" / "prompt is too long"）导致的会话死循环。证据（20… · Audit through DELIVER

Delivered: 修复上下文溢出（API Error 400 "Input is too long" / "prompt is too long"）导致的会话死循环。证据（2026-11-15，会话 54c62829-91aa-46a8-bc35-14b1514dc0ee，transcript \~/.claude/projects/-Users-gawan--swarm-ai-SwarmWS/54c62829-91. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 28m 16s wall · 28m 16s active · no baseline yet (1/5 runs) |
| Quality | WARN | 1/1 verification · 5/6 findings closed (3 test-closed, confirmed by review) |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 540k comparable · 420k input · 120k output · 31.5M cache reads, not counted · 32.1M billed · host-session responses in the run window · no baseline yet (1/5 runs) |

Knowledge: requirement recalled · 8 · design recalled · 6 · cited 1 (+4 found on demand) · searched the DDD 1×

## Decisions

- D1 · How much media to keep after a shed → Strip oldest first until the live media is at most 12 MiB. Why: R2.1 allows up to 16 MiB; 12 MiB leaves about 12 MiB of new screenshots (about 24 at 500 KB) before the next proactive shed at 24 MiB, so a shed (which also rewrites the prompt cache) happens rarely, while the most recent images stay (DESIGN\_SPEC, agent)
- D2 · What counts as live history → Non-sidechain records after the last system compact\_boundary record; image and document blocks at the top level of a user message or inside a tool\_result content list; size = bytes of their base64 data. Why: That is what --resume sends; counting media before a compaction boundary would shed for nothing (DESIGN\_SPEC, agent)
- D3 · Backup naming and retention → shutil.copy2 to '&lt;sid&gt;.jsonl.pre-media-shed-&lt;UTC timestamp&gt;.bak' next to the transcript; never deleted by this change. Why: A byte-for-byte pre-image (DDD a7aaf693); a name that does not end in .jsonl so the CLI never lists it as a session; shedding is rare, and deleting a backup would destroy the only copy of removed screenshots (DESIGN\_SPEC, agent)
- D4 · How the user learns about a shed → A 'reconnecting' SSE event with reason 'context\_too\_large' and a message saying older images were removed to fit the model's request limit, plus the continuation prompt telling the model the same so its answer can mention it. Why: The reconnecting event is the existing channel the frontend already renders during a kill/respawn gap (the leak restart uses it); no new event type or frontend state (DESIGN\_SPEC, agent)
- D5 · Where the proactive measurement runs → After a successful turn in SessionUnit.send, a background task stats the transcript; only if the file is over 24 MiB and grew by at least 1 MiB since the last measure does it scan in asyncio.to\_thread, json-parsing only lines that contain an image/document marker. Why: Keeps the send path and the event loop free (review lens: hot paths); a small transcript costs one stat (DESIGN\_SPEC, agent)
- D6 · Error card action → Add CONTEXT\_TOO\_LARGE to ChatErrorMessage OWN\_ACTION\_CODES so the card shows no Continue button. Why: Continue on an unchanged oversized history would fail again; the card's text names the real actions (Compact, new tab) (DESIGN\_SPEC, agent)
- D7 · How the user sees that images were removed (R2.2) → The reconnecting event carries the text during the respawn, and the continuation prompt tells the model to say it in one sentence; no new persisted notice type. Why: core.turn\_notice lines are shown only when they end a turn (turnZones noticeEnds), so a mid-turn notice would be invisible; adding a new notice kind is a frontend contract change this fix does not need (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · On overflow, send /compact to the live CLI (SessionUnit.compact) and retry. Not taken: Compaction is a model call over the whole history and rewrites it into a summary, losing detail the user's work depends on; whether the CLI strips images before that call is not something SwarmAI controls, and the request that failed was body-size bound, not token bound. Stripping only the pixels keeps every text, tool call and result.
- A3 · Only fix the error text (classify CONTEXT\_TOO\_LARGE, tell the user to open a new tab). Not taken: Leaves the structural cause (nothing bounds media bytes in history) and makes every image-heavy session die at about 32 MB; the user still loses the session's continuity.
- A4 · Block the image Read itself once the session holds too many image bytes (a PreToolUse gate). Not taken: A gate that refuses normal work (reading a screenshot to check it) fails SOUL P7's 'leaves normal work a way through'; shedding old images keeps the work going.

Requirements analysis:

- missing\_edge (R2.1): Stripping the very latest image could remove what the model is about to look at. The strip goes oldest first and stops once under budget, so the most recent images survive whenever they fit. → rewritten
- missing\_edge (R2.3): A token-count overflow (prompt is too long: N tokens &gt; limit) with no media cannot be fixed by stripping; it ends in the terminal message pointing to Compact / a new tab instead of looping. → rewritten
- missing\_edge (R2.1, R2.4): The transcript is the session's only full record; removing image bytes is a destructive edit, so a backup copy is written first (SOUL P4: back up before an irreversible change). The screenshots came from files the agent read, so the marker names the file path when known. → rewritten
- vague (R3.1): Budgets: the CLI's own limit is 33,554,432 bytes (constant in the bundled CLI); the failed request carried about 31.6 MB of messages. Proactive shed at 24 MiB of media, shed down to 16 MiB, so a session sheds rarely and keeps room for text growth. → confirmed
- missing\_edge (R1.1): session\_unit TURN\_ABANDONED (stream abandoned by the reader) also hard-codes the resend advice. An overflow does not reach it: Bedrock's rejection arrives as an is\_error ResultMessage handled in streaming\_orchestrator's result branch (observed in the daemon log as session\_unit.sdk\_error), not as a reader abandonment. Left unchanged. → confirmed

## Review coverage (from each expert's transcript)

- gate0-skeptic · evaluate r1: read 100% of its prompt · 5/25 tool calls
- gate0-skeptic · evaluate r1: read 100% of its prompt · 6/25 tool calls
- security · review r1: read 100% of its prompt · 7/26 tool calls
- correctness · review r1: read 100% of its prompt · 8/26 tool calls
- security · review r2: read 100% of its prompt · 5/25 tool calls
- correctness · review r2: read 100% of its prompt · 9/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 9f53ffc52b60: 8 pass, 0 fail, 0 not run
- test · swarmai @ 05a4f4467489: 7 pass, 0 fail, 0 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pass
  - skills-lint: not run
  - experts-lint: not run
  - desktop-typecheck: pass
  - desktop-contract-lint: pass
  - desktop-build: not run
  - version-check: not run
  - backend-bvt: not run
  - frontend-run-frontend-tests: not run

TEST · swarmai:
- python -m pytest tests/test\_context\_overflow.py tests/test\_bedrock\_thinking\_gateway.py tests/test\_blank\_api\_result\_retry.py tests/test\_pending\_terminal\_undeliverable.py tests/test\_sdk\_auth\_error.py te → pass (199 passed: the change's tests plus the existing tests of the error classifiers, the leak/glitch restarts, send() and the API-error continuation)
- python scripts/smoke\_import.py → pass
- python scripts/lint\_problem\_values.py → pass
- python scripts/security\_scan.py → pass
- npx tsc --noEmit → pass
- npm run lint:contract → pass
- npx vitest run src/components/chat/\_\_tests\_\_/ChatErrorMessage.contextTooLarge.test.tsx src/components/chat/\_\_tests\_\_/ChatErrorMessage.fixSignIn.test.tsx → pass (5 passed (the card the change touches))

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/core/transcript\_media.py
- swarmai: backend/tests/test\_context\_overflow.py
- swarmai: desktop/src/components/chat/\_\_tests\_\_/ChatErrorMessage.contextTooLarge.test.tsx
