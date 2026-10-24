---
title: Welcome screen v2 — one brand, one queue
date: 2026-10-24
created: 2026-10-24
updated: 2026-10-24
project: SwarmAI
status: accepted — mockup approved 2026-10-24
---

# Welcome screen v2 — one brand, one queue

The new-chat Welcome screen (`desktop/src/pages/chat/components/WelcomeScreen.tsx`)
is the starting point of a new conversation. v2 keeps the SwarmAI logo and slogan,
shows what is waiting for the user, and nothing else. Mockup agreed 2026-10-24
(honeycomb brand, light + dark).

## Problems with v1

1. **Two "Needs You" numbers.** Left nav pill = `/api/attention` blocking+review (8);
   Welcome "4 pending" = `/ddd/brains` attention counted per brain, governance
   items excluded.
2. **Brain pulse duplicates the left nav** (9 brains, tier bar, "edited 2d ago").
3. **A third to-do list.** The P0/P1 focus list re-ranks `.context/MEMORY.md` Open
   Threads; it showed stale items as red P0s.
4. **Two click meanings.** A focus item sent its raw title at once; a Working item
   only filled the input.
5. **Wasted backend work.** `/system/briefing` computed signals, hot news, job
   results and SQLite todos that nothing reads; ~28 s cold, on its own pool.
6. **No cache.** `BrainHomeView` and the briefing fetched in a bare `useEffect`, so
   every new tab recomputed the brain gallery (0.4–3.8 s) and never saw Brain Hub's
   invalidation.
7. **Layout jumps.** Three async blocks inside a vertically centred column moved
   the whole page each time one arrived.
8. **Off-system visuals.** Hand-written px sizes, `font-bold`/`font-medium`, emoji,
   hard-coded hex and Tailwind palette colours, and a cyan→purple slogan gradient
   unrelated to the amber honeycomb logo.

## Design

**Content, top to bottom** — top-aligned, inside the shared `chat-column`:

- Logo (`/swarm-avatar.svg`), wordmark `SwarmAI` (display 20/600), slogan
  "Grow your **Brain**. Deliver **Anything**." (heading 14; only the two key words
  carry the brand gradient).
- **Needs you** card: count + up to 3 rows (title, source label). Clicking a row or
  "Open all" opens the Needs You overlay (`show-needs-you`). Nothing is ever sent.
  Empty queue → one calm line. A failed read → the section is hidden (the screen
  must still work as a chat start).
- A faint honeycomb pattern behind the hero, radially masked; a few cells light
  once on mount (~1.1 s, then still; off under `prefers-reduced-motion`).
- **The bee** (added 2026-10-24, mockup approved at 3.6 s with hover): the same
  drawing as the streaming bee (`BeeGlyph`), but it LANDS — the streaming bee never
  lands (a running turn); this one flies in once (3.6 s: soft start, slow approach,
  a short hover, touchdown) and settles on the logo's top-right corner (ready).
  Hovering the landed logo sends it on one 2.6 s lap; hovering mid-flight does
  nothing. Reduced motion, a hidden tab (zero width) or no Web Animations: it just
  sits on the logo. Once landed nothing animates. Geometry is pure
  (`welcomeBee.ts`: Catmull-Rom waypoints in logo coordinates, re-sampled by arc
  length); the flight is a WAAPI `transform` animation — no CSS offset-path.

**One source for the queue.** The Welcome card reads `overlayQuery.needsYou()`
(react-query key `['overlay','needs-you']`). `useAttentionQueue` (the pill's 30 s
poll) moves onto the SAME query with `refetchInterval: 30s`, so the pill, the
overlay and the Welcome card share one cache and always show the same number.
Count = `counts.blocking + counts.review`, the pill's formula.

**Brand colour.** Amber from the logo, used only on the Welcome screen and other
brand touchpoints; app-wide buttons and selected states stay `--color-primary`.
Tokens (per theme, in `index.css`): `--color-brand`, `--color-brand-strong`
(text-safe), `--color-brand-hi`.

**Visual system.** `WelcomeScreen.tsx` joins `navVisualSystem.test.ts` `FILES`
with `KNOWN` staying `{}`: type steps only, weights 400/600, theme tokens only, no
emoji.

## Deleted

- `BrainHomeView.tsx` + test (Brain Hub stays in the left nav).
- The focus / Working / Learning sections and `pages/chat/components/briefing/`.
- `systemService.getBriefing` / `dismissFocus` and their types; ChatPage's
  auto-send `handleFocusClick` and TabView's `onFocusClick` prop.
- Backend `GET /system/briefing`, `POST /system/briefing/dismiss`, their cache and
  background refresh, and the `briefing` executor pool.

## Kept on purpose

- `core.proactive_intelligence.build_session_briefing` (the session-start prompt
  briefing) is a different function and is untouched.
- **Follow-up done (2026-10-24):** `build_session_briefing_data` and everything
  only it used are deleted too — `_extract_working_items`, `_extract_jobs_summary`,
  `_tail_read_lines`, the report/heading/media helpers, `get_focus_keywords` (only
  test mocks referenced it), and the notify-job `source: briefing` Slack formatter
  (`jobs/executor._format_briefing_slack_message`; no system or user job used that
  source). The Signal Digest Slack path and `select_signals` stay.

## Acceptance tests

- Welcome count equals the attention query's blocking+review; at most 3 rows.
- Clicking a row opens `show-needs-you` and sends nothing.
- Rendering the Welcome screen issues no `/system/briefing` or `/ddd/brains` request.
- Pill and Welcome card read one query key (one request for both).
- `navVisualSystem` ratchet stays `{}` with `WelcomeScreen.tsx` in `FILES`.
- Each test is mutation-checked: revert the fix, the test goes red.
