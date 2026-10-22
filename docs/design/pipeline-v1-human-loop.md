---
title: Pipeline v1 human loop — asking, stopping, resuming
status: implemented
date: 2026-10-22
created: 2026-10-22
updated: 2026-10-22
project: SwarmAI
---

# Pipeline v1 human loop

## Problem (observed on main, 2026-10-22)

1. A run waiting for a human decision (`awaiting_human`) shows as `paused` with a
   Resume button; Resume answers 409 because `resume` requires `paused`.
2. The dashboard never shows the question, so the user cannot tell what to answer.
3. Stop and a closed tab leave the run `active`: nothing emits `user_stop` /
   `session_lost`, so the dashboard says "running" while nothing drives it.
4. `decision-answer` is a CLI command the agent can run itself: nothing proves a
   human chose the option.
5. Ownership moved by `run_owner_gate` lives only in `run.json`, which the engine
   rewrites from `contract.session_id`; every human authorization (`run-land`,
   accept-finding, discard-snapshot) stays bound to the session that created the run,
   so a run continued in another tab cannot be landed by a human there.

## Principles

- **Only a human writes `human_answer`, and only the daemon writes it.** The agent
  can ask, never answer. Two entry points, both in the daemon process: the chat
  question card (AskUserQuestion answered by a person, not the channel auto-answer)
  and the dashboard decision card.
- **A run says who drives it.** Ownership is a signed event chain
  (`contract.session_id`, then `owner_transfer` events). Every authorization binds to
  the current owner.
- **Stopping is a fact, not an inference.** An explicit Stop or a closed tab is
  recorded (`user_stop` / `session_lost`) and pauses an `active` run; a run waiting
  for a human keeps waiting. Anything else that leaves an `active` run undriven
  (crash, daemon restart, a turn that simply ended) is shown as *idle* from owner
  liveness, without writing an event.
- **Every dashboard action ends in the owning chat.** Resume / Continue / answering
  a decision open the owner session and put a "continue run X" prompt in its
  composer; the user sends it. The dashboard never drives an agent behind the
  user's back.

## States the user sees

| Run status / owner        | Dashboard           | Actions                                   |
|---------------------------|---------------------|-------------------------------------------|
| active, owner streaming   | running             | none                                      |
| active, owner not live    | idle                | Continue in session                       |
| awaiting_human            | needs your decision | the question + one button per option      |
| paused (user_stop / session_lost) | stopped     | Resume (then Continue in session)         |
| paused (worktree_unavailable)     | worktree unavailable | Resume (retries worktree creation)  |

## Flows

**Decision.** Agent runs `decision-request`; the CLI returns `ask_user_question`
(question carries the marker `[<request_id>]`, one option per declared option,
label `"<id> — <choice>"`). The agent calls AskUserQuestion with it and ends the
turn. When a person picks an option, `ask_question_gate` (daemon) records
`human_answer` on the run this session owns, then the agent continues. Free text,
an expired card or a channel auto-answer records nothing: the run stays
`awaiting_human` and the agent re-asks. The dashboard card writes the same event
through `POST /api/pipelines/{id}/decision`. `pipeline_v1.py status` returns the
pending `ask_user_question` so a later turn can re-ask the same question.

**Stop.** User Stop on the owning session: `user_stop` (active → paused). Closing
the owner's tab: `session_lost`. Both are no-ops while `awaiting_human`. A paused run
continues only after an explicit `resume` (CLI or dashboard).

**Next day.** Same tab: send "continue run X" (or answer the card). Another tab:
its first owner command (`status` is read-only; `stage-start`, `resume`, ...)
records `owner_transfer` when the old owner is not streaming; from then on that tab
owns every authorization, including human landing.

## Residual risk

The daemon's HTTP API is unauthenticated on loopback. `run_owner_gate` denies Bash
calls to the human-only decision endpoint, but a determined agent process could still
reach it; this is the same file/loopback trust root already accepted for
`authority.key` (deviations #5).
