# # Fix: chat keyword extraction cuts 会话 down to 话 · Audit through DELIVER

Delivered: # Fix: chat keyword extraction cuts 会话 down to 话 \*\*Evidence (2026-11-16).\*\* \`backend/core/recall\_filter.py\` splits each CJK run on \`\_CJK\_STOP\_WORDS\` (around 120-147); the list contains single characte. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | PASS | 28m 8s wall · 28m 8s active · 56.6% budget |
| Quality | WARN | 1/1 verification · 2 not run · 1/3 findings closed (5 reported; within 10 lines of one file they count once) (1 test-closed, confirmed by review) · 2 fail-to-pass |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | PASS | 359k comparable · 260k input · 99k output · 14.0M cache reads, not counted · 14.3M billed · host-session responses in the run window · 36.7% budget |

Knowledge: requirement recalled · 6 · design recalled · 7 · cited 4 (+2 found on demand) · searched the DDD 2×

## Decisions

- D1 · Which words go in the list? → Common work-chat words that hold one of the one-character stop words and are content, not filler (会话 能力 对话 说明 本地 上下文 下载 性能 技能 过期 不一致 ...); filler such as 没有/是否/了解 stays out. Why: Smallest list that covers the task's words and the general-Chinese term-map keys measured as damaged; a structural test refuses a listed word that holds no one-character stop word (it would be dead weight) (DESIGN\_SPEC, agent)
- D2 · Keep \_CJK\_STOP\_RE? → Delete it and its re-export from session\_router. Why: After the change nothing reads it (grep: only the re-export), and a second regex describing the cut would drift from the real one (DESIGN\_SPEC, agent)
- D3 · How is a piece longer than 4 characters chunked when it holds a kept word? → Pieces of at most 4 characters stay one term as before; a longer piece is cut around its kept words first, then the rest in 4-character chunks. Why: Measured: making every kept word its own term doubled the Chinese weight of 会话隔离 and pushed p1 from rank 1 to 3; greedy whole-token packing lost 重启 in 用摘要重启. This rule has no regression on the probe (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Treat a one-character stop word as a cut only at a run edge. Not taken: Fails the task's own example: 会 opens the run 会话卡住, so 会话 is still cut to 话卡住; and inside a run it keeps filler such as 的 / 了 in terms (我的会话 -&gt; one term with 的).
- A3 · Use the Brain term map's keys as the protected words. Not taken: extract\_query\_keywords is Brain-agnostic (chat, pipeline run knowledge, ddd\_recall\_eval call it with no Brain); most Brains have no map, so every other user keeps the bug. The list in A1 already holds the general-Chinese keys the SwarmAI map loses today.
- A4 · jieba or another segmenter. Not taken: Rejected by an earlier measured decision (about 89MB RSS and 0.73s init on a RAM-capped daemon, no recall gain).

Requirements analysis:

- missing\_edge (E1): The run-edge rule the task offers fails the task's own example: 会 opens the run 会话卡住, so cutting one-character stop words at run edges still yields 话卡住. A protected word list matched longest-first at each position (forward maximum matching, the same scan re.split already does) is used instead. → rewritten
- missing\_edge (E1): The Brain term map's keys are per-Brain and extract\_query\_keywords is Brain-agnostic (also used by pipeline run knowledge and ddd\_recall\_eval); a built-in list of common words that contain a one-character stop word works for every user. Measured: 11 of the SwarmAI term map's 211 keys are damaged today (会话, 对话, 本地, 下载, 上下文, 过期, 技能, 性能, 不一致...); the general-Chinese ones go in the list. → rewritten
- missing\_edge (E2): A listed word must not swallow a stop word that starts earlier: leftmost match decides first (可以下载 still cuts 可以), and only at the same start position does the longer listed word win over a shorter stop word. → rewritten
- missing\_edge (E1): Gate-0: after the stop-word cut a piece longer than four characters is cut into fixed 4-character terms, which can break a listed word at a chunk boundary (x本地会话 -&gt; x本地会 + 话). The chunker packs whole tokens instead: a listed word is never split, and a piece holding no listed word is chunked exactly as before. → rewritten

## Review coverage (from each expert's transcript)

- gate0-skeptic · evaluate r1: read 100% of its prompt · 7/25 tool calls
- correctness · review r1: read 100% of its prompt · 6/25 tool calls
- correctness · review r2: read 100% of its prompt · 7/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 0350de523285: 6 pass, 0 fail, 0 not run
- test · swarmai @ 2cc2e63768d5: 4 pass, 0 fail, 2 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pass
  - skills-lint: not run
  - experts-lint: not run
  - desktop-typecheck: not run
  - desktop-contract-lint: not run
  - desktop-build: not run
  - version-check: not run
  - backend-bvt: not_run
  - frontend-run-frontend-tests: not run

TEST · swarmai:
- perl -e 'alarm 115; exec @ARGV' /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_85198d91815e/repos/swarmai/backend/.venv/bin/python -m pytest tests/test\_recall\_filter.py tests/test\_recall\_injection.py test → pass (210 tests: the change's tests (incl. fail-to-pass test\_cjk\_words\_holding\_a\_one\_char\_stop\_word\_survive) and every caller's)
- python scripts/smoke\_import.py → pass (640 modules import)
- python scripts/lint\_problem\_values.py → pass (ok)
- python scripts/security\_scan.py → pass (no new HIGH findings)
- bash scripts/run\_bvt.sh → not\_run (53 curated files, none touching recall\_filter; run once as the task check on the landed branch instead of twice)
- python scripts/lint\_skills.py; python scripts/lint\_experts.py; desktop tsc / lint:contract / build / vitest; bash scripts/sync-version.sh check → not\_run (the change touches no skill, expert, desktop or version file (backend/core/recall\_filter.py, session\_router.py re-export, two backend test files))

## Not run (the agent's reason, not checked)

- swarmai · bash scripts/run\_bvt.sh: 53 curated files, none touching recall\_filter; run once as the task check on the landed branch instead of twice
- swarmai · python scripts/lint\_skills.py; python scripts/lint\_experts.py; desktop tsc / lint:contract / build / vitest; bash scripts/sync-version.sh check: the change touches no skill, expert, desktop or version file (backend/core/recall\_filter.py, session\_router.py re-export, two backend test files)
