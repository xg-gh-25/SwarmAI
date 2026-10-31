---
title: "Swarm's cognition level: is it needing the person less"
created: 2026-10-31
updated: 2026-10-31
project: SwarmAI
status: implemented (r1 + correctness review fixes)
---

# Swarm's cognition level

Mockup: `Knowledge/Mockups/2026-10-31-needs-you-cognition.html` (approved by XG 2026-10-31,
"按这版做，回补 4 周"). Companion of `2026-10-31-cdf-health.md`.

## 1. What it answers

Under the queue in the first Needs You tab: is Swarm needing the person less? The first tab
holds what the person cares about: what needs them now, and whether the system as a whole is
getting better. It measures outcomes only. How much is stored, or how many corrections were
logged, rises whatever happens and says nothing about judgment.

## 2. What is shown

1. One sentence and the share of this week's conversations the person did not have to
   correct; below it, how many of this week's corrections repeat one made before.
2. A weekly curve of both. Back-filled weeks are dashed. Vertical marks show where the rules
   the agent reads changed. A trend ("getting better / worse / about the same", ±5 points) is
   claimed only between weeks measured the same way (recorded vs back-filled); otherwise the
   sentence says it is too early.
3. "Where its mistakes come from": the newest monthly brain mistake audit as one chain
   (learned and kept → put in front of it → used right → still true), each link with the
   mistakes it leaked and what to improve; the largest link is red.
4. Whether written knowledge gets used (`status.north_star`), and a link to the Health tab.

## 3. Sources (no new measurement except the back-fill)

| What | From |
|---|---|
| corrected sessions | `queries.read_corrections`: the corrections the after-session review recorded, and from when the record covers (`covered_from`) |
| sessions per week | `data.db` messages with role `user`, by local ISO week (stored local, no offset) |
| back-fill | `cdf_health.db` `correction_backfill`: one judge verdict per session-week that the record does not cover, up to `BACKFILL_WEEKS = 4` weeks back |
| repeats | the review's `repeats` declaration on a recorded correction |
| marks | `cdf_health.db` `rule_marks`: each health pass records the hash of `.context/{SOUL,AGENT,SELF,STEERING}.md` and the product version; a change is a mark; the first pass's are `:first` and not shown. Plus the day the record began |
| mistakes | the newest `Knowledge/JobResults/<YYYY-MM>-brain-mistake-audit.json`, else a baseline report |
| used | `status.north_star` |

A recorded correction belongs to the week of the session's last message from the person at
or before the review recorded it (a review can run the next week). A week: a session
corrected in the record counts corrected. A week starting at or after
`covered_from` counts the others as not corrected. A week starting before it takes the
others from the back-fill; a session neither recorded nor judged is not counted (`known`).
A week with fewer than 3 known sessions has no rate.

## 4. The back-fill

Run by each health pass (job or re-check), once per session-week: a Bedrock call
(`feature=cdf.health`) given only the person's own messages of that session and week, each an
escaped JSON string inside its tag, asking whether the person corrected the agent at least
once (a new request, a next step, approval or refining their own request is not). A week still
running is judged again when it has more messages. A failed call leaves the session-week
for the next pass, up to 3 attempts. Marks are a plain log (a change back is a mark too). Cost on this instance: 143 session-weeks,
about 8 minutes, a few dollars, once.

## 5. Not in this change

- An attention item when the curve falls: later, once there are several recorded weeks.
- Product-source rule changes (SOUL/AGENT edits ship as a version) are marked only through
  the projected `.context` copies and the version, not by commit.

## 6. Verification

Tests `backend/tests/test_cognition_level.py` and `desktop/.../CognitionLevel.test.tsx`:
week composition (record, covered, back-fill, minimum), back-fill scope (only uncovered,
once, failure left, only the person's messages, escaped), back-fill horizon, marks only for
changes, audit selection (newest month, previous, baseline), route model, trend only between
like-measured weeks, placement under the queue. Each guard mutation-checked.
