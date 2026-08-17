# Desktop Prewarm — Maintained Warm Pool (design v3)

**Date:** 2026-08-17 · **Supersedes:** the fill-once pool shipped in run_f107f442 (方案A)
+ hardened in run_4e881e96 (6 findings). **Status:** ⛔ **DEFERRED / NOT-PURSUED**
(decided 2026-08-18, run_824c437e).

> **⛔ DEFERRED — do NOT treat as an active plan.** After designing this maintained-warm-pool
> (two adversarial rounds: NO-GO → Scope-A → CONDITIONAL-GO), the ROI did not justify building
> it now. The only benefit is "全新 (no-history) tab first-message 33s→~2-3s" — a LOW-frequency
> action (real usage is reopen-history-tab, which Scope-A excludes), and prewarm only removes the
> ~12s SDK handshake, not the dominant inference time. Cost = a real session-spawn hot-path
> refactor (send() COLD branch → acquire-from-pool + self-replenish + reconcile loop + concurrency
> guards) + ~400-800MB resident. Instead, the fill-once prewarm was TURNED OFF
> (`SWARM_DESKTOP_PREWARM=0`, run_824c437e) — clean cold-start baseline, no leak, no half-built
> subsystem masking the "spawn is slow" root question. This design is ARCHIVED for the day the ROI
> flips (high-frequency new-tab usage, or a multi-user build). If revived: start from this doc on a
> clean baseline, and first investigate whether the 12s `__aenter__` handshake can be made cheaper
> (which could make the whole pool unnecessary). Until then, this is a record, not a task.

> Provenance note: the frontend showed an open `2026-08-16-desktop-tab-prewarm-design-v2.md`
> that does not exist on disk (a stale UI reference — designs/ has no prewarm doc; newest
> was 08-09). This v3 is the first on-disk prewarm design. It captures XG's directed
> architecture from the 2026-08-17 session, grounded in live production evidence.

---

## 1. Problem — the current pool is fill-once, effectively a摆设

**Live evidence (2026-08-17, flags on, session d943546f window):**
- daemon start → `warm_desktop_pool(depth=2)` fired ONCE (main.py:1264), 2 units ready
  at 22:21:09 / 22:21:20.
- Nothing ever re-warms. Pool is monotonically decreasing: consume/expire one → -1, never +1.
- New tab `ffcd60b3` at 22:26:20 (a genuinely fresh tab, msg_count=1) → hit slot0, found
  it STALE (spawned 22:21, 311s > 60s TTL) → discarded → **fell through to cold spawn →
  TTFT 33.5s**. Zero prewarm benefit.
- slot1 (`prewarm-2c8df642`, 405MB) sat IDLE for 40+ min, never adopted, never reclaimed —
  because the stale path only kills the ONE slot the adopt scan hit (`:2114` breaks on
  first bucket match), never checks the other.

**Root class:** the pool is FILL-ONCE (warm at startup, drain forever). Combined with a
60s TTL and a ctx-hash that changes on any `.context/*.md` write, the invariant "a fresh
warm unit is waiting when the user acts" holds only for the first default tab in the first
~60s. After that: structural miss. XG: "现在的就是个摆设."

**Hard facts (measured, drive the design — do not re-guess):**
| Fact | Value | Source |
|---|---|---|
| spawn cost (SDK `__aenter__` handshake) | ~10–12s | `spawn_perf wrapper_aenter_ms` 9744–12190 |
| RAM per prewarm unit | ~405MB (peak ~598MB) | `memory_sample prewarm-=405MB` |
| current TTL | 60.0s | session_router.py:1750 |
| ctx_hash source | mtime_ns of `.context/*.md` (glob, non-recursive) | :2006–2015 |
| re-warm triggers today | 1 (daemon lifespan only) | main.py:1264 |

---

## 2. Target architecture — a DECOUPLED self-replenishing subprocess supplier (XG-directed)

### The reframe (XG's core insight, 2026-08-17)

The pool must be a **self-replenishing supplier of GENERIC warm subprocesses**, fully
DECOUPLED from chat/session logic. The consumer does NOT tell the pool "I'm a new tab" or
"I'm a cold-resume" — **the pool doesn't care who's asking or why.** A consumer just
`acquire()`s a warm subprocess when it needs one; the pool hands one over and immediately
starts replenishing. XG: "你不需要去判断是不是 new tab, 是不是 cold resume, 你需要的时候直接
去取就好了 不影响任何其它操作 —— 这个是我们最应该考虑的."

### Why the pool unit is GENERIC — and the HARD BOUNDARY on who may consume it

A pooled subprocess is spawned by `prewarm_channel_session` (session_router.py:1912-1917),
which passes ONLY `agent_config` + `enable_skills` + `enable_mcp` + `channel_context=None` to
`build_options` — NO `editor_context`, NO `session_context`, NO `resume_session_id`, and
`needs_context_injection` is never set. So the pooled unit's `system_prompt` is a genuinely
session-independent **default-baseline** prompt. That is what makes it fungible: a brand-new
no-history tab can take any pooled unit and it's correct.

> ⚠️ **CAUTION (design constraint, not a general property):** `build_options` does NOT split
> cleanly on its own — `editor_context`, the resume block, active-session digest, and pending
> suggestions ARE rendered INTO `system_prompt` at spawn (`prompt_builder.py:1108-1161,1965`)
> when passed. The pool unit is generic ONLY because `prewarm_channel_session` passes none of
> them. Any future change that makes prewarm pass session context would break fungibility.
> (Minor known gap: the baseline prompt bakes time-varying non-session ephemera — briefing/
> digest/suggestions — that `_desktop_ctx_hash` (`:2015`, `.context/*.md` mtimes only) does
> NOT track, so a pooled unit can carry a slightly stale briefing yet be judged "fresh." Low
> impact; noted for the pipeline.)

### 🚫 SCOPE BOUNDARY (Scope A — corrected after the 2026-08-17 adversarial NO-GO)

**Only a genuinely-new NO-HISTORY tab may consume the pool. A history-bearing (cold-resume)
tab MUST NOT — it keeps the current cold/`_ensure_spawned`+`--resume` path.**

**Why (verified against source — this KILLED the earlier "resume consumes the pool too"
premise):** adopting a pooled unit re-keys a live **IDLE** subprocess (`adopt_prewarmed_unit`
requires `state==IDLE`, session_router.py:1961-1979). But history recovery via mechanism B
is gated on `is_cold_resume`, which requires **`state==COLD`** (`:3038-3042`). An adopted
unit is IDLE, not COLD → `is_cold_resume` False → desktop has no `channel_context` →
`needs_channel_resume` False → `needs_context_injection` never set → the history block is
NEVER built (`_prepend_resume_to_query` gated off, `:3339`/`:426-438`). WORSE, the
`_adopted_prewarm_fresh` flag (`:1979`) makes turn-1 take the warm-reuse `client.query()`
path which DISCARDS `system_prompt` entirely. Net: routing a history tab through acquire →
**silent, total loss of its prior conversation.** This is exactly why the existing adopt path
already REFUSES history-bearing sessions (`_try_adopt_desktop_pool` returns False when
`count_by_session(session_id) >= 1`, `:2100-2106`) — the history-guard from run_4e881e96 #1.

**The earlier claim "mechanism B composes with adopt" was FALSE.** The blocker is not
mechanism A vs B — it is that adopt yields IDLE and mechanism B is structurally gated on
COLD. Scope A keeps that guard intact: pool serves the highest-frequency win (new-tab cold
start) without ever touching the data-loss-prone resume path. Making resume pool-eligible
would require redesigning the adopt seam to (a) force `needs_context_injection` on an IDLE
adopted unit AND (b) bypass the turn-1 warm-reuse discard for history tabs — deferred as a
SEPARATE future effort, out of scope here.

### The invariant (the whole design in one line)

> **The pool self-maintains 1–2 (memory-adaptive) GENERIC warm IDLE subprocesses at all
> times. A NO-HISTORY new tab `acquire()`s one when needed; acquire triggers immediate async
> replenish. The pool is the SOLE spawn source for the NEW-NO-HISTORY-tab first-message path.
> History-bearing (cold-resume) and revive paths keep their current `_ensure_spawned` route
> (Scope A boundary above).**

### 2.1 The supplier interface (the decoupling seam)

The pool exposes ONE method:

```
async def acquire_warm_unit(session_id, agent_id, model) -> SessionUnit | None
```
- Returns a ready IDLE subprocess re-keyed to `session_id` (generic default-baseline),
  or `None` if none available within the pending timeout (→ consumer handles per §2.3).
- **Once inside the seam, the pool is agnostic to WHO acquires** — it never inspects the
  consumer's kind. The ONE eligibility decision (history vs no-history) happens at the CALL
  SITE, ABOVE the seam, using the existing `count_by_session >= 1` guard (`:2100`): a
  history tab does not call acquire at all. This preserves both properties — the pool stays
  identity-agnostic internally, AND the data-loss boundary (Scope A) is enforced before the
  seam is reached.
- Internally: hand over the head unit → fire-and-forget `_ensure_pool_watermark()` (replenish).

### 2.2 Self-replenishment (async background — and the `_spawn_lock` contention it MUST respect)

`_ensure_pool_watermark()` is a **fire-and-forget background task** (XG point 2: "必须有
异步方式来补充 spawn 池子, 不能影响当前 chat tab 和 session 任何地方"):
- Runs OFF the request path — a live `send()` does not `await` the replenish task itself.
- Loops: `while pool_size < target AND spawn_budget.can_spawn: spawn one into pool`.
- `target` is **memory-adaptive (XG-directed)** — NOT a hardcoded 2. Derived from
  `resource_monitor.spawn_budget` (the SAME single memory authority, P8), capped at
  `MAX_DEPTH=2`: RAM ample → 2 · medium → 1 · tight → 0 (pending §2.3 covers the miss).

> ⚠️ **`_spawn_lock` contention — the adversarial NO-GO's CLAIM-6 (verified, must be honored,
> NOT hand-waved).** `_spawn` acquires a **module-level `asyncio.Lock` `_spawn_lock`**
> (session_unit.py:127) held across the full ~12s `wrapper.__aenter__()` handshake (:3165),
> and it serializes **ALL** SessionUnit spawns — replenish spawns AND real user cold spawns
> go through the same lock. So "never touches live chat/session" is FALSE as an absolute: if
> replenish is mid-spawn (~12s under the lock) and a real session hits the cold path, the
> real spawn BLOCKS behind replenish (`spawn_perf lock_wait_ms` would show it). **The design
> does NOT claim zero contention — it BOUNDS it:**
> - **Yield-to-real-work rule:** replenish checks for any pending REAL spawn before acquiring
>   `_spawn_lock`, and defers (re-queues itself) if one is waiting. A prewarm is a luxury; it
>   must never make a real user's spawn wait. (Impl: a lightweight "real-spawn-pending"
>   counter/flag on the router, checked at the top of the replenish spawn; if set → skip this
>   round, the reconcile loop §3.1 retries next tick.)
> - **Worst-case bound if the yield races:** ≤ one spawn (~12s) of added `lock_wait` on a real
>   spawn — the SAME latency that spawn already costs, never unbounded. Acknowledged, bounded,
>   not denied.
> This is the honest version of XG's "不影响任何其它操作": replenish yields to real work and
> its worst-case interference is bounded to one spawn duration, rather than a false "zero."

### 2.3 Pending on acquire (no cold-start fallback branch)

- **Opening a tab NEVER pends** — pure frontend shell, instant.
- **First message (acquire time)**: if the pool has a unit → instant hand-over. If empty
  (replacement still warming, or RAM-gated to 0) → **pending: wait for the pool to produce
  one**, bounded by `ACQUIRE_TIMEOUT ≈ spawn cost (~15s)`. On timeout → spawn one directly
  INTO the pool and consume it (still the one path — the pool spawns it, not a parallel
  cold branch). XG's insight: worst case = a cold-start's feel; **no downside vs today**.

> ⚠️ **Pending lock discipline (adversarial CLAIM-5, must honor).** The pending wait lives in
> the `run_conversation` first-message path. It MUST be a clean `await` on an
> `asyncio.Event`/`Condition` that holds **NEITHER `_slot_lock` NOR `_spawn_lock`** while
> waiting — a 15s wait under `_slot_lock` would serialize every other tab's slot acquisition
> (`_acquire_chat_slot`/adopt/evict all take it). The wait yields the event loop cleanly, so
> other sessions' coroutines are unaffected. UX gap (noted for pipeline): the existing
> "queued" SSE indicator is for slot contention; a first-message pool-warm pend has no
> dedicated affordance yet — acceptable (silent ≤15s = cold-start feel), refine if needed.

### 2.4 The discriminator — `_sdk_session_id`, NOT "new tab vs resume"

Routing uses TWO existing signals (both already computed in `run_conversation`, no new
per-consumer "kind" flag): `_sdk_session_id` (has this unit ever had a live subprocess?) and
`count_by_session` (does this session have prior history?). The pool serves EXACTLY ONE cell:

| `send()` COLD state | history | Route | Why |
|---|---|---|---|
| `_sdk_session_id is None` | **count ≤ 0** (no history) | **ACQUIRE from pool** | first-time, no history to recover — a generic default-baseline unit is exactly right |
| `_sdk_session_id is None` | **count ≥ 1** (cold-resume) | `_ensure_spawned` direct (current path) — NOT pool | mechanism-B history needs `state==COLD`; an adopted pool unit is IDLE → history lost (Scope A) |
| `_sdk_session_id` set | (any) | `_ensure_spawned` direct + `--resume` | REVIVE — a live session's subprocess died; mechanism A, bound at spawn (§2.5) |

**Why this still satisfies XG's decoupling requirement:** the ONE eligibility check
(`count_by_session >= 1` — the run_4e881e96 #1 guard, already at `session_router.py:2100`)
lives at the CALL SITE, ABOVE the `acquire_warm_unit` seam. Inside the seam the pool remains
100% identity-agnostic (it never inspects who's asking). So "the pool doesn't judge who" holds
for the pool; the history-vs-not gate is a caller-side admission check, not pool logic. New
no-history tabs — the highest-frequency case — get the full pool benefit; resume/revive keep
the proven-safe current path. (Making resume pool-eligible = the deferred future effort in the
Scope A box above: it requires redesigning adopt to inject mechanism-B on an IDLE unit + bypass
the turn-1 warm-reuse discard — out of scope here.)

### 2.5 The ONE genuine exception — REVIVE (`_sdk_session_id` set)

When `_crash_to_cold_async(clear_identity=False)` fires (stop, poison-guard, OOM, watchdog),
it PRESERVES `_sdk_session_id` (session_unit.py:1975/1985) — the mark of "a live subprocess
existed and died." The next `send()` sees COLD + id-set → revives via `_ensure_spawned` with
`--resume=id` (mechanism A). This does NOT consume the pool: it is a *specific* session
reviving its *own* conversation, not a first-time need for a generic subprocess. It is NOT
"spawn scattered everywhere" (XG's worry) — it's the SAME `_ensure_spawned` primitive; only
the FIRST-TIME path (`_sdk_session_id is None`, both new-tab and cold-resume) is re-routed
through the pool. Both paths share `_ensure_spawned`'s robustness (§2.6).

### 2.6 Robustness lives in the spawn primitive, not a parallel escape (XG point 1)

XG: "如果它都 spawn 不出来 你在其它地方也搞不出来啊." Correct — a pool that can't spawn and a
`send()` that can't spawn share the SAME `_ensure_spawned`; a "spawn elsewhere on timeout"
fallback is fake redundancy (REMOVED from the earlier draft). What we harden is
`_ensure_pool_watermark`'s spawn robustness (bounded retry, budget-gate, loud-on-fail), which
benefits every consumer. And per XG point 3: **if replenish can't spawn, the ONLY cause is
insufficient resources — which is exactly when a new tab should NOT open anyway.** Pool
capacity thus doubles as the admission signal (§2.2's budget-adaptive target makes this
automatic — no separate "can I open a tab" check needed).

---

## 3. Mechanism — ONE replenish primitive, driven by acquire + maintenance triggers

Everything routes through a single background primitive (the supplier's engine, §2.2):

```
async def _ensure_pool_watermark():   # fire-and-forget, off the request path
    while pool_size < target(spawn_budget) and spawn_budget.can_spawn and pool_size < MAX_DEPTH:
        spawn one generic desktop-baseline unit into the pool
```
`target` is memory-adaptive via `resource_monitor.spawn_budget` — the SINGLE memory
authority (P8, no second door). Idempotent, throttled, budget-gated, never holds a
request-path lock. It is called from the triggers below — but the primitive is the same;
these are just *when* it fires, not different spawn paths.

| # | Trigger | Where | Why |
|---|---------|-------|-----|
| T1 | **daemon start** | main.py lifespan (existing :1264) | initial fill to target |
| T2 | **acquire consumes a unit** | `acquire_warm_unit` success path (§2.1) | replenish immediately — the CORE self-maintenance loop (consume→replenish keeps 1 in hand while the replacement warms) |
| T3 | **unit retired** (TTL / ctx-hash stale) | the stale-scan branch (fixes B1) | replenish the retired slot so the pool self-heals instead of draining |
| T4 | **open-tab signal** | `PUT /open-tabs` when tab COUNT increases | belt-and-suspenders top-up BEFORE the user types (tightens the "open then immediately type" hit-rate) |

**T2 is the load-bearing one** — it is the self-replenishing heart (§2.1: acquire hands over
+ fires replenish). T1 seeds; T3 self-heals staleness; T4 is a hit-rate nudge. All four call
the SAME `_ensure_pool_watermark` — decoupled from any consumer's identity.

**T4 detail:** `save_open_tabs` (settings.py:258) already fires when the frontend adds a
tab. Gate the replenish on "tab count went UP" (not every reorder/close write), debounce
(coalesce rapid writes), only spawn if below target. This is the "prewarm follows open-tab"
behavior XG asked for — a top-up nudge, NOT the sole mechanism (T2 already maintains the
invariant on every consume).

**RSS-eviction note (not a replenish trigger):** under memory pressure a pool unit may be
reclaimed (run_4e881e96 #4 — prewarm is NOT exempt from RSS survival kills). That is correct
and does NOT trigger replenish (replenish is budget-gated and would just be denied under the
same pressure). The pool shrinks under pressure by design (§2.2: RAM-tight → target 0), and
pending (§2.3) covers the resulting miss.

### 3.1 THE GUARANTEE — why events alone are NOT enough, and reconcile is (the load-bearing part)

Event-driven replenish (T1–T4) is FAST but does NOT by itself guarantee "always 1–2 waiting."
Five leaks make an events-only design drain silently:

1. **Spawn lag:** replenish takes ~12s; the pool is short during that window. (depth=2 keeps
   one in hand — mitigates, doesn't eliminate.)
2. **Replenish failed, no retry:** a fire-and-forget spawn that raises (SDK error / budget
   deny) just vanishes — the slot is never refilled by any event.
3. **A pooled unit dies silently:** an IDLE pool subprocess can die (SDK crash, OS kill) with
   NO acquire happening — so no T2, no event, no refill. Nobody is watching.
4. **Stale-but-unconsumed:** a ctx-hash change makes a pooled unit stale; if it's only
   detected at acquire time, an idle pool sits full-of-stale and refills nothing until a tab opens.
5. **Quiescence:** app open, user idle — zero events fire, so the pool's health is unmanaged.

**The fix (standard replica-maintenance / reconcile-loop pattern): events for SPEED,
a periodic RECONCILE for the GUARANTEE.**

> **`_check_pool_watermark` — a new periodic step in the EXISTING maintenance loop**
> (`lifecycle_manager._maintenance_loop`, runs every `LOOP_INTERVAL=60s`; already hosts
> `_check_ttl` / `_check_orphan_sessions` / `_check_memory_pressure`). Each tick it does a
> DECLARATIVE reconcile: **count the FRESH (non-stale, alive, IDLE) pool units; if fewer than
> `target`, retire any stale ones and FIRE-AND-FORGET `_ensure_pool_watermark` to refill.**
>
> ⚠️ **MUST be `asyncio.create_task(...)`, NEVER `await` (adversarial CLAIM-4, verified).**
> `_maintenance_loop` (lifecycle_manager.py:281-293) runs its steps STRICTLY SERIAL within a
> tick. `_ensure_pool_watermark` awaits a ~12s spawn under `_spawn_lock`; if the reconcile
> step `await`ed it, it would stall every subsequent step in the tick — including
> `_check_streaming_timeout` (a 12s blind spot in streaming-hang detection) and the orphan
> reaper — and push out the next tick. So the reconcile step ONLY: (1) counts fresh units +
> retires stale (fast, no spawn), (2) `create_task(_ensure_pool_watermark())` (detached) if
> below target. The spawn happens off the loop coroutine. Cap: at most one in-flight
> replenish task (idempotent guard) so ticks don't pile up tasks. The `_check_memory_pressure`
> step already sits after; reconcile placed so its (fast) counting doesn't delay pressure relief.

This is declarative, not imperative: we don't say "on event X, spawn one" (that leaks) — we
declare "there SHALL be `target` fresh units" and the loop drags reality back to the
declaration every tick. It closes ALL five leaks: failed replenish (#2) → next tick re-spawns;
silent death (#3) → next tick sees fewer alive → refills; stale-unconsumed (#4) → not counted
as fresh → retired+refilled; quiescence (#5) → the tick is time-driven, needs no event.

**Two-layer contract:**
- **Event-driven (T1–T4)** = the fast path — keeps the pool full in real time so a normal
  acquire hits instantly. Without it you'd wait up to 60s after each consume.
- **Reconcile (`_check_pool_watermark`, 60s)** = the guarantee — the pool CONVERGES to target
  regardless of what events fired or failed. Without it, any leak above drains the pool permanently.

**Honest bound:** if BOTH the event-path AND the just-failed replenish miss, the worst-case
gap before reconcile heals it is one loop interval (≤60s); an acquire in that window pends
(§2.3, cold-start feel). Reconcile guarantees *eventual* convergence, not *every-instant*
fullness. Shortening `LOOP_INTERVAL` would tighten the window at a constant-cost — NOT worth
it: event-driven already covers the common case, reconcile is the rare-leak backstop.
Reuses the existing loop — no new scheduler/timer (P8: one scheduling authority).

### Bugs to fix in the same change (independent of the architecture, but in-scope)

- **B1 — stale scan only kills the hit slot (`:2114` break-on-first).** Must scan ALL slots
  in the bucket: retire every stale one + refill. Today slot1 leaks 405MB indefinitely.
- **B2 — dead depth-accounting (`:2046` `have = sum(... if k == key)` compares 4-tuple keys
  to a 3-tuple key → always 0).** Gate-2 (run_4e881e96) flagged it LOW; fold the fix in here
  since we're rewriting the pool-maintenance loop anyway.

### Concurrency-safety of the replenish primitive (must-fix — from the 2026-08-17 re-review CONDITIONAL-GO)

The move from fill-once to a self-replenishing pool with MULTIPLE trigger paths (T2 event +
§3.1 reconcile, both `create_task`) introduces concurrency hazards the old single-shot warmer
never had. These are pipeline Gate items — verified against source, must be nailed, not
hand-waved:

- **C1 — Single SHARED atomic in-flight-replenish guard across ALL paths.** T2/T3/T4 (event)
  AND `_check_pool_watermark` (reconcile) all call `_ensure_pool_watermark`. Two concurrent
  runs both see `pool_size < target` → both spawn. The "at most one in-flight replenish" cap
  MUST be a single router-level handle/flag, set+cleared under a lock (or a CAS on one shared
  `Task`), checked identically by every path. A per-path guard = double-spawn.
- **C2 — Watermark spawn must claim its slot ATOMICALLY (kill-on-overshoot, never bare
  `break`).** VERIFIED live: current `warm_desktop_pool` (`session_router.py:2051` spawns pid,
  then `:2060` `if slot >= depth: break`) — on a raced overshoot the already-spawned `pid` is
  neither stored NOR killed → a 405MB prewarm-prefixed subprocess leaks (TTL/orphan-exempt per
  `:2149`) until RSS pressure. SAME leak class as B1. Fix: reserve the slot BEFORE spawn (count
  a reservation toward target), or on overshoot `await unit.kill()` the extra + drop from
  `_units` (never a bare break that abandons a live pid).
- **C3 — Detached reconcile task must self-guard + `finally`-clear the guard.** VERIFIED: the
  maintenance loop's per-tick `try/except` (`lifecycle_manager.py:324`) does NOT cover
  `create_task`-detached work (only `_loop_task` is referenced). A raising `_ensure_pool_watermark`
  would vanish silently AND could wedge the C1 guard forever. Fix: the replenish body wraps
  itself in try/except (loud-on-fail, §2.6) and clears the in-flight guard in a `finally`.

### Tuning (maintained pool changes what these mean)

- **TTL 60s → 300s.** In a MAINTAINED pool, TTL is only a staleness backstop, not the
  primary lifecycle (T3 refills on retire). 300s reduces needless churn; the pool stays
  fresh by refilling, not by short TTL.
- **ctx-hash change → retire stale + REFILL (not clear-and-wait).** On a `.context/*.md`
  change, the固化 baseline prompt is genuinely stale, so retiring is correct — but T3 must
  immediately refill with the NEW ctx, so the pool self-heals instead of going empty. This
  is what covers the high-frequency-commit (dev) scenario that fill-once structurally can't.

---

## 4. Resolved decisions (settled with XG this session — 2026-08-17)

1. **pending-on-acquire safety timeout — RESOLVED.** Pending waits on a pool-available
   signal, bounded by `ACQUIRE_TIMEOUT ≈ spawn cost (~15s)`; on timeout → spawn directly
   INTO the pool then consume (one path, no separate cold branch — just a synchronous fill).
   Worst case = a cold-start's feel, no downside vs today.

2. **T4 open-tab coupling — RESOLVED: include it.** T2 (consume→replenish) already maintains
   the invariant; T4 is the top-up that makes "open tab → type immediately" reliably hit.
   Ship it in this design.

3. **Budget gate under RAM pressure — RESOLVED: accept the degradation.** Replenish is
   `spawn_budget`-gated (prewarm yields to real RAM demand, run_4e881e96 #4). Under pressure
   the target drops (→1→0); pending covers the miss. This is the existing safety posture —
   NOT a new cost control (STEERING #2 does not apply: it's a RAM-survival gate on a luxury
   subprocess, not a per-call budget cap on real work).

4. **Watermark is memory-adaptive, not a fixed number — RESOLVED (XG-directed).** "用 1 个
   还是 2 个 根据系统内存情况来定." target = derived from `spawn_budget` up to MAX_DEPTH=2
   (§2.2). No hardcoded watermark; the single memory authority decides.

### Still to confirm during EVALUATE/Gate-1 (not blocking the design)

- **#2 (run_4e881e96 de-scope) re-assessment** (§5c): does the maintained pool make
  "graceful yield of a sole prewarm" safe now (its blocker — pool-goes-empty — is gone via
  T2/T3 replenish)? Decide at EVALUATE.

---

## 5. Scope / non-goals — the COMPLETE remaining-gap sweep (nothing dropped)

This session surfaced several loose ends across the prewarm work. ALL of them are collected
here so none is forgotten (XG: "把 remaining gaps 也放到 scope 里 别忘了清").

### 5a. Core architecture (this design's main body)
- `_ensure_pool_watermark` primitive + the 4 refill triggers T1–T4 (§3).
- **pending-on-adopt** — remove the cold-fallback branch; a session only consumes the pool
  (§2 decision 2 + §4 Q1 timeout).
- **TTL 60s → 300s** (backstop only in a maintained pool).
- **ctx-hash change → retire stale + REFILL** (self-heal, covers high-freq-commit).

### 5b. Independent bugs found this session (fix in the same pipeline — in-scope)
- **B1 — stale scan kills only the hit slot** (`session_router.py:2114` break-on-first) →
  the other slot leaks (~405MB, observed 40+ min idle). Fix: scan ALL bucket slots on
  retire + refill each.
- **B2 — dead depth-accounting** (`:2046` `have = sum(... if k == key)` 4-tuple-vs-3-tuple
  → always 0, break at `:2047` is dead code). Gate-2 (run_4e881e96) rated LOW; fold in
  since we rewrite the maintenance loop.

### 5c. Re-evaluate the run_4e881e96 #2 de-scope (its blocker is GONE in v3)
- **#2 was de-scoped** because "graceful evict of a SOLE prewarm would reverse the XG
  force=True anti-starvation contract, and after evicting the pool goes empty." In the
  MAINTAINED pool that second half no longer holds: **T2/T3 refill immediately after any
  consume/retire**, so a graceful yield of a sole prewarm self-heals. **Re-assess in this
  design:** does the maintained-pool invariant make "graceful yield sole prewarm + refill"
  safe now (removing the ≤QUEUE_TIMEOUT wait the #2 finding identified), WITHOUT reversing
  the anti-starvation guarantee for REAL tabs (R6 orphan-only still protects live tabs)?
  → Likely yes; decide during EVALUATE/Gate-1. `evict_deferred` (:2553) is the code to revisit.

### 5d. Doc / bookkeeping gaps (must clear, not code)
- **IMPROVEMENT.md "What to Watch For" entry** (added 2026-08-17) describes the FILL-ONCE
  behavior + "history tabs cold/--resume". The history-tab half stays true, but the
  "pool doesn't refill / new tab may cold-start" half is SUPERSEDED by this v3. Update that
  entry (or add a v3 note) when this design ships — don't leave a stale contract on record.
- **Unpushed commits:** `707c1435` (6-findings fix) + `1cedf371` (flag-enable) are local,
  未 push (STEERING #5 — push is XG-initiated). The v3 pipeline's commits will stack on
  these; push decision is XG's at the end.

### Non-goals (unchanged)
- **Adopt/resume CORRECTNESS** — run_4e881e96's #1/#3/#4/#5/#6 hold; history tabs still route
  to cold/--resume via the history-guard. This design is POOL MAINTENANCE, not the
  adopt-vs-resume decision.
- **Multi-bucket prewarm** — still main bucket only (desktop/default/default-model).

---

## 6. Verification contract (for the pipeline)

- **Self-maintenance invariant:** after start + N acquires, the pool holds `target` fresh
  IDLE generic units (unless RAM-gated below it). Mutation: disable T2 replenish → invariant
  RED (pool drains monotonically, the exact v2 bug).
- **Scope-A boundary test (data-loss guard — the load-bearing correctness property):** a
  no-history new tab CONSUMES the pool; a history-bearing (count≥1) tab does NOT — it takes
  the current cold/`--resume` path and its prior conversation is fully recovered. Mutation:
  route a history tab through `acquire_warm_unit` → assert its history is LOST (adopted unit
  is IDLE, mechanism-B gated on COLD never fires) → the test must catch it RED. This is the
  exact regression the adversarial NO-GO identified; the guard (`count_by_session>=1` at the
  call site, above the seam) is what prevents it.
- **Pool identity-agnostic test:** inside `acquire_warm_unit`, no branch inspects consumer
  kind — the eligibility decision is entirely at the call site. (Seam stays decoupled.)
- **Pending test:** acquire on an empty pool WAITS for the pool to produce one and consumes
  it (does NOT open a parallel cold-spawn path). On timeout it spawns INTO the pool then
  consumes. Mutation: restore an independent cold-fallback branch → test sees a non-pool
  spawn → RED.
- **Memory-adaptive target:** with `spawn_budget` mocked ample → target 2; medium → 1;
  tight → 0. Mutation: hardcode target=2 → the tight-RAM case RED.
- **B1 test:** two stale slots → BOTH retired + replenished (not just the scanned one).
  Mutation: restore break-on-first → the second stale slot leaks → RED.
- **§2.5 exception preserved:** stop-resume / OOM-respawn of a LIVE session still uses
  `_ensure_spawned` directly (NOT the pool) — a live session's revive is not re-routed.
- **Hit-rate smoke (real system):** open tab, wait 5s (typing), send → `acquire` hits a pool
  unit (log shows pool consume, NOT a cold spawn), TTFT << 33s. Open a second tab
  back-to-back → also hits (target=2 kept one in hand).
- **flag OFF:** byte-identical no-op (whole subsystem gated on SWARM_DESKTOP_PREWARM).
