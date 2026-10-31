---
title: Swarm Out — a Hive pipeline run's decision reaches the Host
created: 2026-10-31
updated: 2026-10-31
project: SwarmAI
status: implemented
---

# Swarm Out: a Hive pipeline run's decision reaches the Host

Parent designs:
`2026-10-27-swarm-out-v0-dispatch.md` §3.3 (the S3 bridge), `pipeline-v1-human-loop.md`
(decisions, `human_answer`).

## 1. Problem

A pipeline run on a Hive that cannot decide alone records a DC-1 decision and goes to
`awaiting_human`. The agent then asks it on an AskUserQuestion card whose question ends
with `[<request_id>]` and whose labels are `<id> — <choice>`. A person's pick on that
card is recorded by the ask gate (`security_hooks` → `pipeline_human.record_card_answer`).

Before this change the bridge mirrored only what was on the prompt board, so on the Host:

- the decision was visible only while that card was open. Once the turn ended, the card
  expired (4 h) or the Hive restarted, the run still waited, but nothing reached the
  Host and the run's stage showed "not running" instead of "waiting for you";
- the card was mirrored as a plain question, cut to 4000 / 500 characters. A cut question
  lost its marker and a cut label no longer matched, so the pick was delivered but never
  recorded;
- free text typed on the card was delivered and recorded nothing;
- the decision's context was lost: why the evidence was not enough, each option's
  consequence, irreversible, high risk.

## 2. Change: the waiting decisions are one snapshot

A run's own state is the truth about what waits. So a decision gets no lifecycle of its
own on the bridge: no `ask/`, no `closed/` markers, and no reconcile. An earlier version
gave it that lifecycle. Two review rounds then found decisions that stayed open forever or
were hidden forever, because those markers drifted from the run's state. The snapshot
removes that whole class of failure.

**Hive (`core/swarm_out_decisions.py`, called once per bridge pass).**

- **Reading.** `pipeline_human.pending_decisions` reads every waiting decision from the
  runs' event logs: the run, its owner session, the owner generation (the number of
  `owner_transfer` events) and when the decision was asked. The read is all or nothing:
  if any run cannot be read, that pass changes nothing. An unreadable run is never taken
  for one that stopped waiting.
- **Writing.** The whole list (at most 50) is written to `bridge/<hive>/decisions.json`,
  replacing the previous one, and only when it changed. The Hive role may write that one
  key (`hive/provisioner.py`; a Hive update rewrites the role policy).
- **Keys.** An entry's key is `dc-<sha256(run, owner, owner generation, decision)[:32]>`.
  It stays the same across restarts, so an answer written before a restart still applies.
  Each takeover gives a new key, even a takeover back to an earlier owner.
- **The run's own card.** A board question in the same session whose text ends with the
  decision's marker is not mirrored as a separate question, so the decision shows once.
  While the runs cannot be read, the cards known from the last readable pass stay hidden.
- **Truncation.** A field longer than the card shows (request id 200, question 4000,
  reason 2000, choice 500, consequence 1000 characters) marks the entry `truncated`. Such
  an entry is not answered from the Host: a person never picks an option they could not
  read in full.

**An answer** is `answer/<owner session>/<key>.json`, written once, by the Host only. It
names an option (`option_id`) and what the card showed (`shown_sha256` =
`decision_digest`). The digest covers the run, request id, question, reason, and each
option's id, choice, consequence, irreversible flag and risk. The Host computes it over
the entry it shows; the Hive computes it over the entry rebuilt from the run. So an
answer can only reach the option the person read. A wrong option, a different digest, a
truncated entry, or a pick the run refuses (`EngineError`) is listed as `refused`, and
nothing is recorded. Delivery:

- **Card open:** the card gets `{<the card's whole question>: "<id> — <choice>"}` as a
  person's answer. The ask gate records it, and the waiting turn continues. If the run is
  still waiting two passes later, the gate did not record it; the answer is then recorded
  directly (no-card path), and the card stays hidden in the meantime.
- **No card:** the pick is recorded on the run
  (`answer_from_dashboard(source="swarm_out")`). Any other failure to record is tried
  again on the next pass, 3 times in all. Then the session gets the dashboard's continue
  message (`pipeline_human.continue_prompt`) with the option id. The wording is fixed: it
  does not speak as the person and carries none of the run's own text. It is sent with
  the client id `swarm-out-<key>`. Once the router answers `accepted`, the message
  belongs to the server, which keeps it and delivers it (queued for a busy session).
  Until then it is sent again on later passes under the same client id, 3 times in all;
  a repeat is recognised as the same send.

**Host (`core/swarm_out.py`).**

- **Listing.** `_read_decisions` reads the snapshot. `_clean_decision` keeps only known
  fields, capped the way the Hive caps them, and drops an entry whose option ids are not
  2–3 short distinct strings or whose key is not `dc-`. A decision written into `ask/` is
  not shown. An unreadable snapshot keeps the other questions and is reported ("could not
  read the decisions of Hive …").
- **Run state.** `run_state` reports `waiting` / `decision` for a run with a listed
  decision that has not been refused, whatever the session reports, so settle derives
  `needs_answer`. A stopped or deleted Hive is not read.
- **Answering.** `answer` (`dc-` requests) finds the entry in the snapshot. It refuses a
  refused or truncated entry, an option that is not the entry's, and a digest that does
  not match, then writes once (`IfNoneMatch`).

**Panel.** `AskCard` shows a decision with its reason. Each option is a button showing
its consequence and any irreversible or high-risk tag; nothing can be typed. A pick sends
`option_id` and `shown_sha256`. A truncated decision shows no options and says to answer
it on the Hive's own page.

## 3. What is unchanged, and what this does not close

- Plain AskUserQuestion prompts and approvals keep the ask/closed bridge as before.
- The answer route stays the person's (`ask.answer` in `core/swarm_out_ops.py`). Swarm
  Run's lead run answering a child's decision through `internal:conductor` is a separate
  step (Swarm Run design §5.6).
- "Person-only" is only as strong as the ops registry's first phase
  (`2026-10-31-swarm-out-ops-registry.md` §2.4). An agent on the Host that reads the
  account's credentials can write `answer/` itself, and the Hive then records that as the
  person's pick; this change makes those known routes reach a pipeline `human_answer`.
  Closing them is phase 2 of the ops registry.
- Known edge cases:
  - If the runs have never been readable since the bridge started, the run's card is
    mirrored as a plain question until the first readable pass.
  - When a card answer was not recorded and is then recorded directly, a card that is
    still open shows as a plain question afterwards. Answering it unblocks the turn and
    records nothing.

## 4. Verification

`tests/test_swarm_out_hive_decisions.py`, after one correctness and one security review
of the first version and a second correctness review that led to the snapshot:

- **Hive snapshot:** listed while the run waits and gone after; written only on change;
  a pass that cannot read every run changes nothing; `pending_decisions` is all or
  nothing; the card is hidden (only in its own session); truncation; an overlong option
  id; a new key for each takeover; the answer goes to the new owner.
- **Hive answering:**
  - delivery: on the card with the exact label; a card answer the run did not take is
    recorded directly; no card means record plus a continue message in fixed words;
  - refusals: a rewritten card, a wrong option or digest, a refusal by the run;
  - retries: passing record failures (`OSError`, `ValueError`, `LookupError`) are tried
    again, then refused after the tries run out; an accepted continue is never sent again
    (queued, or failing later); an unaccepted one is sent again under the same id, then
    given up;
  - robustness: a restart keeps the answer; a failed snapshot write does not stop the
    prompts.
- **Host:** the same digest as the Hive; cleaning; malformed entries; truncation; `ask/`
  never carries a decision; the snapshot is listed exactly; an unreadable snapshot keeps
  the questions; `run_state`; the answer and every refusal.
- **End to end on a real v1 run:** both paths land a `human_answer`.

Removing any one fix turns a test red: all 28 mutations were caught. The existing bridge,
Host, binding, dashboard, human-loop, settle, provisioner and panel suites stay green.
The Hive needs an update, which brings both the new bridge and the role policy.
