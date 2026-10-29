---
title: A Brain's Runs — one card, real lanes, one count of what waits
date: 2026-10-30
created: 2026-10-30
updated: 2026-10-30
status: built
---

# A Brain's Runs — one card, real lanes, one count of what waits

## 1. What is wrong (observed on the SwarmAI Brain, 2026-10-30)

- **Conductor · Runs is half static.** The Chat tab lane is real (the 30-day pipeline
  analytics). "Local Worker 0/1 slots" and "Hive 0/2 slots" are hard-coded mocks; the
  Hive is real since Swarm Out (`/api/swarm-out/runs`, each run carries the Brain of its
  Project Agent), and no Local Worker exists or is planned.
- **A local Run row and a Hive run card answer different questions.** The row has the
  requirement, the profile, `stage 8/8` and the wall minutes. The Hive card has where it
  ran, the Brain and Project Agent, how it ended, active time, billed tokens and cost,
  human decisions, failed steps, the pipeline's audit verdicts and the reply. The local
  record holds most of the same facts (`run.json`: `audit_summary`, `audit_rows`,
  `human_decisions`, `finished_at`, `landing` / `reason`, `stage_attempts`; the
  `billed_tokens` / `book_cost_usd` metrics in the event log) but the summary drops them.
- **The Overview map draws mocks:** Slack and Schedule sources, "Local Worker and Hive
  are mocks". **The Eval tab** has a `Conductor (mock)` section (queue wait, worker busy)
  and a `Project Agent (mock)` section, though human decisions per Run are recorded.
- **Two counts of "needs you".** The header says 1 (the attention channel's per-Brain
  pending: here a repository the Brain started reading), the map and its panel say 0
  (`needsYouItems` lists Runs, proposals, escalations, jobs and the agent only).

## 2. Design

**D1. One card, the Hive card's dimensions.** A Brain's Runs tab shows each Run with the
same facts in the same order, whichever lane ran it: the status dot, title and time;
where it ran (Chat tab / the Hive's name) and its state; Brain · Project Agent (profile,
mode); how it ended (landed / not landed and why / running / needs you); then cells —
active time, billed tokens, cost, human decisions, rework (local: stages re-run; Hive:
failed steps), and the audit verdicts (Isolation, Speed, Quality …) when the pipeline
wrote them. A fact nobody recorded shows `—`, never 0. A Hive run in this tab IS the
Swarm Out `RunCard` (one component, so the two can never disagree again); a local Run is
a `LocalRunCard` built from the same cell component (`Stats`).

**D2. Real lanes only.** Chat tab (this machine's pipeline Runs) and Hive (Swarm Out runs
whose Project Agent is this Brain). The Local Worker lane is deleted, not labelled: there
is no worker to wait for. The Hive lane says so when no Hive run went out for this Brain.

**D3. The map draws what exists.** Request sources: Chat tab and Hive, each with its count.
Slack and Schedule are removed (no path sends a Brain's Run from them today). The Run
Conductor line reads `Chat tab N · Hive N running`, no mock text.

**D4. One count.** `needsYouItems` takes the attention channel's per-source split
(`pendingSplit`) for everything it does not list item by item — escalations, jobs, newly
read repositories (a notice), other sources — so the panel, the map's You and the header
count the same items.

**D5. Eval tab without mocks.** The Conductor section is deleted. Project Agent reads
`Human decisions per Run` from the records (local `human_decisions`, Hive approvals +
denials + answered questions); the unmeasured `Change requests` stat is deleted.

## 3. Backend

- `RunEngine._read_model` adds `usage.billed_tokens` and `usage.book_cost_usd` from the
  latest `billed_tokens` / `book_cost_usd` metric events (absent on older runs).
- `PipelineRunSummary` gains `finished_at`, `active_s`, `billed_tokens`, `cost_usd`,
  `human_decisions`, `landing`, `not_landed_reason`, `rework`, `audit` (dim, verdict and the
  number behind it, so the local card carries the same Autonomy Judge tag as a Hive card).
  All optional: an old projection gives null.

## 4. Verification plan

- Backend: a projection with the two metric events carries them; `_summary` of a real
  `run.json` shape fills every new field and of a bare one leaves them null (tests).
- Frontend: the Runs tab renders a Hive run of this Brain with the Swarm Out card and none
  of another Brain; a local card shows the cells and `—` for unknowns; no `mock` text
  anywhere in Overview / Runs / Eval; the needs count of a Brain whose only pending item
  is a newly read repository is 1 on the map and in the panel.
- Each test fails with its fix reverted (mutation check).
- Review: none beyond the tests — read-only projections of records that exist, no write
  path, no shared state machine.

## 5. Limits (as built)

- A Hive card in a Brain's Runs tab opens its Hive session like Swarm Out's: ChatPage
  publishes `openHiveSession` on the overlay bridge, the Brain Hub surface provides it
  (`overlaySessionOpener`: close the Hub, then open the Hive tab under it). A run with no
  session this Host knows shows no Open, as in Swarm Out.
- The map's Run dots are both lanes', newest first, at most twelve.
- A Hive run older than 30 days is not shown, as the Chat tab lane.
