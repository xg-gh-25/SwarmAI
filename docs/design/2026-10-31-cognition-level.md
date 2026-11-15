---
title: "Swarm's cognition level: is it needing the person less"
created: 2026-10-31
updated: 2026-10-31
project: SwarmAI
status: implemented (r2: one instrument for every week)
---

# Swarm's cognition level

Mockup: `Knowledge/Mockups/2026-10-31-needs-you-cognition.html` (approved by XG 2026-10-31,
"按这版做，回补 4 周"). Companion of `2026-10-31-cdf-health.md`.

r2 (XG: "先不考虑cost 我们先保证这个东西有用"): r1 measured back-filled weeks with a judge
and recorded weeks with the review's record. The two instruments differ (the review records
every small correction), so the curve could not show a trend: this week read 28% against
33-61% before, an artifact of the switch. Now every shown week is judged the same way, the
review's record checks the judge (of 14 conversations it recorded a correction in, the judge
found 13), and the curve's line and the headline pool four weeks (single weeks swing by
20-40 points), weighted by conversations.

## 1. What it answers

Under the queue in the first Needs You tab: is Swarm needing the person less? The first tab
holds what the person cares about: what needs them now, and whether the system as a whole is
getting better. It measures outcomes only. How much is stored, or how many corrections were
logged, rises whatever happens and says nothing about judgment.

## 2. What is shown

1. One sentence and the share of this week's conversations the person did not have to
   correct; below it, how many of this week's corrections repeat one made before.
2. A weekly curve of both. Back-filled weeks are dashed. Vertical marks show where the rules
   the agent reads changed.
   The trend is computed once, in the backend (`cognition_level.trend`, r3 2026-11-15): the
   last four weeks pooled against the four before, with a 95% interval for the difference of
   the two corrected shares (Agresti-Caffo: one corrected and one clean session added to each
   window, so a window at 0% or 100% still has a width). It says "better" or "worse" only
   when the interval excludes zero, otherwise "no visible change (±X points)" with X the
   interval's half-width; a window with fewer than 10 judged sessions gives no direction
   ("too few sessions for a trend"). The prompt headline (`core.north_star`), the C&M rail
   and this tab all read that one result and word it the same way
   (`backend/tests/fixtures/north_star_trend_wording.json` holds both sides to it). Before
   r3 the headline compared one week with the mean of the earlier weeks within ±5 points and
   the tab four weeks with four at ±5 points; at about 36 sessions a week the noise is about
   ±16 points for one week and ±11 for four, so both called noise a trend.
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
changes, audit selection (newest month, previous, baseline), route model, placement under the
queue. Each guard mutation-checked. The trend
(r3): `test_the_trend_is_called_only_outside_its_interval` (the interval, its width at 0%,
the 10-judged minimum), and the shared wording in `test_north_star.py` and
`CognitionLevel.test.tsx` over one case file.
