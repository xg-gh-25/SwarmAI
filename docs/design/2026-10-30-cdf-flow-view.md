---
title: Cognition flow view — the CDF dashboard read backward
created: 2026-10-30
updated: 2026-10-30
project: SwarmAI
status: implemented (XG approved the mock Knowledge/Mockups/2026-10-30-cdf-flow-redesign.html, asked for a Guideline tab)
replaces: the Flow page of 2026-10-26-swarm-cognitive-distillation-flow.md §10 (tile wall, JSON previews)
---

# Cognition flow view

## 0. Why

The first Flow page showed what the data has, not what a person asks: a wall of equal
tiles (Inbox, 整理, 卡 0–3 …), extraction coverage per source id, and candidates as raw JSON.
Its stage counts were also wrong once the agent-decides change landed: `json_type(…) IS
NOT NULL` counts a stored JSON `null`, so every card read as "ran" for every unit.

## 1. What a person asks, in order

Layout (revised 2026-10-31, XG: no tabs inside a tab, a dashboard with trends on top): the
Need You overlay has one level of tabs — Needs you · Cognition flow · How it works — and
is `xl` like the other overlays. Cognition flow reads top to bottom: status and window →
dashboard (captured, learned, reinforced, not kept: each its change against the window
before and its trend, an hour per bar for 24h, a day for 7d / 30d; "learned" stands out) →
anomalies → where it landed | how it decides, side by side → the item list → health.


1. **What did it learn, and is anything wrong?** One sentence (learned, reinforced, not
   kept and the two main reasons, waiting or failed) and the anomalies worth a look. Each
   anomaly sends a prompt to chat; there are no approval buttons (§10 of the CDF design).
2. **Where did it land?** Read backward from the result, by influence: files read every
   turn → project brains (down to the file) → General Brain & Library, on one scale. A
   click filters the list. The count of items moved out of every-turn files says why.
3. **How was it decided?** The steps `run_unit_v2` actually runs — captured (per source),
   basic checks, pick the place, trust floor, compare, written — with what each let
   through, dropped or moved. Outcomes from the retired v1 reviewer and the one-time
   backlog discard are named separately.
4. **What exactly?** One row per unit: kind/type, whole title and text, and one line
   "→ place · decision". Opening it shows the trace: landed (open file), why this place,
   trust, compared, came from, raw record (redact for the user's own words, 24 h).
5. **How does it work?** The Guideline sub-tab: the flow diagram and the rules, written
   from the running code (no counts or dates).

## 2. Data

- `GET /api/ingestion/landed?window=` (`core/ingestion/flow_view.landed_overview`):
  `series` (one bucket per hour / day of the window), `previous` (the window before;
  null when the store does not reach that far back, and no change is claimed), totals, groups, sources, steps, landed rows (store / project / path), moved
  (from → to, written ones), attention (`failed`, `waiting_long` — a parked deferral is not
  late —, `breaker`, `no_cleanup`, `links_skipped`, `spelling`).
- A reinforcement lands where the entry it is the same as is: `pipeline_v2` records
  `same_path` / `same_anchor` in the decision, and the view counts and filters by it.
- Groups follow the step that decided: `known` (already there), `withdrawn` (you withdrew
  it), `older` (an existing entry is newer), `place` (a Hive lesson about another Brain);
  outcomes of earlier versions (`no_project`, `retired`, `backlog`) are named apart.
- `GET /api/ingestion/units` (`list_units`): outcome, place, source, store / project /
  path / path prefix (where it really landed), keyword, window; pages of at most 50; title
  and text whole. Every string of both responses is secret-redacted, and a keyword matches
  the redacted text only. A prompt sent to chat is built from counts and our own words.
- `reason_group(state, reason)` is the only mapping from a stored outcome to its step;
  the frontend phrases a group and never re-derives one.
- Health, extraction coverage, model calls, the review table and the weekly history stay
  on the existing endpoints, behind "Details".

All reads use `connect_ro` (mode=ro, query_only) on the nav executor, as before.

## 3. Verification

- Backend `tests/test_cdf_flow_view.py`: every outcome has one step; totals, steps,
  landed and moved add up; anomalies only when their condition holds; filters; whole
  text; redaction; the database bytes are unchanged; the routes validate. Mutation-checked.
- Frontend `CognitiveDistillationFlow.test.tsx` against the real English strings.
- Real data rendered in a browser (en and zh) before landing.
