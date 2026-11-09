# 两项对所有用户成立的确定性机制（XG 2026-11-09 选定走 pipeline）：(1) Stop hook 检查回复语言：复用 UserPromptS… · Audit through DELIVER

Delivered: 两项对所有用户成立的确定性机制（XG 2026-11-09 选定走 pipeline）：(1) Stop hook 检查回复语言：复用 UserPromptSubmit 的 \_classify\_message\_language；用户本条消息是中/日/韩文而最终回复不是，就挡一次让 agent 用该语言重写；理由里写明用户明确要求别的语言时保持原样；stop\_hook\_active 第二次一律放行（. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | PASS | 18m 48s wall · 18m 48s active · 12.3% budget |
| Quality | PASS | 1/1 verification · 4/4 findings closed (2 test-closed, confirmed by review) |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | PASS | 400k comparable · 326k input · 75k output · 19.1M cache reads, not counted · 19.5M billed · host-session responses in the run window · 9.8% budget |

Knowledge: requirement recalled · 9 · design recalled · 7 · cited 1 (+1 found on demand) · searched the DDD 2×

## Decisions

- D1 · Compare a markdown rewrite with git HEAD or with the version the Write replaces? → Snapshot the existing file at PreToolUse and compare with that.. Why: HEAD would flag cuts made in earlier uncommitted revisions and skip untracked files; the snapshot is exactly what this Write replaced and needs no git call. (DESIGN\_SPEC, agent)
- D2 · How does the Stop hook know the turn's user language? → The UserPromptSubmit enforcement injector records the classification it already computes in session\_context; the Stop hook reads it.. Why: One classification of the user's text (the injector already strips injected blocks); the Stop input carries no prompt. (DESIGN\_SPEC, agent)
- D3 · Which hook class in the roster? → ADVISORY for the Stop check and the rewrite note, OBSERVE for the snapshot.. Why: The Stop check asks for one rewrite and then lets the stop through; it refuses no tool call, so it is not a SAFETY, SESSION or RUN gate. (DESIGN\_SPEC, agent)
- D1 · Which answer languages hold the stop of a CJK turn? → Only an answer the R19 classifier calls Latin; an answer in another CJK script, or too short to classify, passes. Why: A Chinese answer that quotes a Japanese name or uses kana can be classified ja; holding it would ask a correct answer to be rewritten. The measured failure (69 of 1474 Chinese turns) is all English answers. (BUILD report · swarmai, agent)
- D2 · What does the injector record for a turn it cannot classify? → None, set at the start of every UserPromptSubmit before anything else. Why: Otherwise the previous turn's language would carry over and a short or empty next message could hold an English answer the user wanted. (BUILD report · swarmai, agent)
- D3 · Which paths does the rewrite check read? → Only an existing regular .md file of at most 1 MB that the Write itself names, never through a symlink, never under .context, .artifacts or Knowledge/DailyActivity; read-only, nothing written. Why: The file is the one the Write is about to replace, so the hook reads nothing the tool call would not touch; the symlink skip keeps it from reading a target elsewhere. (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Strengthen the per-turn UserPromptSubmit language reminder and add a prose rule about keeping content in rewrites.. Not taken: The per-turn reminder already runs every turn and 69 of 1474 Chinese turns since 2026-10-26 still ended in English; content-loss in rewrites already has two EVOLUTION entries that did not stop it. More prose is the lowest rung.
- A3 · A model judge at Stop that reads the answer and decides whether it is in the right language and whether a rewrite lost content.. Not taken: A model verdict is not reproducible (SOUL P5) and costs a model call on every turn; the script classifier and a text diff answer both questions deterministically and cost nothing when all is well.

Requirements analysis:

- missing\_edge (R1.1): The answer already streamed to the user before Stop; a block makes the agent append a rewritten answer after it, so the user sees both. Accepted: the last answer is the one in their language. Measured since 2026-10-26: 69 of 1474 turns with a Chinese user message ended in an English answer (4.7%); every sampled one was genuinely English. → confirmed
- missing\_edge (R1.2): A user asking for output in another language (translate this into English) would be blocked once; the reason says to keep the answer when the user asked for it, and stop\_hook\_active lets the second stop through, so the cost is at most one extra model turn. → rewritten
- missing\_edge (R2.1): Comparing with git HEAD would flag cuts made in earlier uncommitted revisions and skip untracked files; the replaced version is snapshotted at PreToolUse instead, keyed by the tool use id, and released at PostToolUse. → rewritten
- vague (R2.1): 'Dropped' means the item's exact text no longer occurs anywhere in the new content (a heading moved or reworded counts as dropped only when its text is gone). → rewritten

## Review coverage (from each expert's transcript)

- security · review r1: read 100% of its prompt · 5/25 tool calls
- correctness · review r1: read 100% of its prompt · 11/25 tool calls
- security · review r2: read 100% of its prompt · 4/25 tool calls
- correctness · review r2: read 100% of its prompt · 3/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 51a9268da6a5: 2 pass, 0 fail, 0 not run
- test · swarmai @ f2ac570429c0: 1 pass, 0 fail, 0 not run

TEST · swarmai:
- perl -e 'alarm 200; exec @ARGV' /Users/gawan/Desktop/SwarmAI-Workspace/swarmai/backend/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test\_reply\_language\_check.py tests/test\_doc\_rewrite\_check → pass

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/tests/test\_doc\_rewrite\_check.py
- swarmai: backend/tests/test\_reply\_language\_check.py
