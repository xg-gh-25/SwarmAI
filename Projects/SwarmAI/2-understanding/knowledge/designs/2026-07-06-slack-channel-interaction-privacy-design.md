---
title: SwarmAI Slack Channel — Interaction & Privacy-Isolation Design
date: 2026-07-06
status: design + Phase-0-VERIFIED (2026-07-06) — current-state baseline established; 2 real gaps found
project: SwarmAI
pipeline_run: run_7fa9aa63
phase0_run: this session (2026-07-06, live channels/ + core/ code read)
author: Swarm
governance_anchors: [STEERING #6, C041, COE02, Shepherd-finding-bdd39e84]
competitive_anchor: MeshClaw/KiroClaw (code read 2026-07-06, mainline)
supersedes: none
---

# SwarmAI Slack Channel — Interaction & Privacy-Isolation Design

> **Scope.** How SwarmAI (acting **on XG's behalf**) behaves in a Slack **team
> channel** with multiple allowlisted non-owner users: when it replies, how it
> tells people apart, and — the hard part — how it structurally guarantees XG's
> private information never leaks to those users while still being *useful* to them.
>
> **Phase 0 is DONE (2026-07-06).** The current-state claims below are no longer
> beliefs — they were **verified against live `channels/` + `core/` code this
> session** (§6 carries the observed results with file:line evidence). Headline:
> the three structural defenses (exclusion matrix, sender identity, file-sandbox)
> are **real and wired on the live path** — the design's foundation holds. Two real
> gaps were found (egress redaction absent; MCP not scoped by sender-tier) — both
> **P1**, neither a foundation collapse. See **§6 (results)** and **§9 (gaps)**.

---

## 1. North Star — three guarantees that must hold *simultaneously*

In a team channel, SwarmAI-as-XG must satisfy all three at once. Any design that
achieves two but not the third is rejected.

1. **不打扰 (Don't intrude).** It does not reply to every message. It enters on an
   explicit signal (@mention), but once *in* a conversation it can follow the thread
   naturally without re-@ each turn.
2. **认得人 (Know who's speaking).** Every inbound message is attributed to a
   specific sender; context, permissions, and history are scoped **per user**. Two
   different people @-ing in the same channel are never conflated.
3. **守得住 (Hold the line on XG's private info).** XG's private data
   (USER/EVOLUTION/personal MEMORY) is **structurally** unreachable by non-owner
   users — enforced by code paths and fail-closed defaults, **not** by asking the
   LLM nicely in a prompt.

The tension is between (3) and usefulness: a bot that hides *everything* to be safe
is useless to the team. §4 (the two-lane model) is the whole design's crux — it
resolves this by separating "what must never leak" from "what we *want* to share."

---

## 2. Competitive anchor — why we cannot copy MeshClaw's privacy model

MeshClaw (becoming open-source **KiroClaw**) has the most mature internal Slack
integration I have read (`slack/gateway.py` ~4269 lines, `slack/handler.py` ~4221
lines, read 2026-07-06). Its interaction design (§3) is directly borrowable. Its
**privacy design is not**, for one structural reason:

> **MeshClaw is strictly single-owner.** `slack/events.py:634` carries the invariant
> `# Invariant: _allowed_users contains only the owner (multi-user disabled)`, and
> `init_socket_mode` disables Slack entirely if `MESHCLAW_OWNER_ID` is unset. Only
> the owner can talk to it; every other sender gets an ephemeral "not authorized"
> rejection (`_route_message`, events.py:1927) and never reaches processing.

So MeshClaw **never has to answer "how do I serve user B without leaking user A's /
the owner's private data?"** — it sidesteps the entire cross-user privacy problem by
having exactly one user. Its privacy measures (allowlist gate, `observe`-records-
only-authorized, Enterprise-Grid origin check, DM-only dashboard links, egress
redaction) are all *single-principal* protections.

**Our problem is one level harder.** STEERING #6 requires SwarmAI to act on XG's
behalf **for a set of allowlisted non-owner users**, while keeping XG's private
brain (USER/MEMORY/EVOLUTION) invisible to them. That cross-user isolation is
exactly the part MeshClaw has no answer for — **it is the part we must build
ourselves and make structural** (§4).

---

## 3. Four-layer architecture

An inbound Slack message flows through four layers. Each layer answers one question,
cites the MeshClaw source it borrows from (where applicable), and the SwarmAI lesson
it must satisfy.

```
Slack event
  → [L1 ACTIVATION]  Should we reply at all?      (off / observe / mention / review + thread_follow)
  → [L2 IDENTITY]    Who is speaking?              (sender_id → display_name → permission tier)
  → [L3 ISOLATION]   What brain can THIS person see? (two lanes: private fail-closed / shared active-inject)
  → [L4 EGRESS]      Is the outgoing reply safe?    (redact + refuse external actions)
```

### Layer 1 — Activation (borrow from MeshClaw)

**Question:** should the bot respond to this message?

**Design:** per-channel `activation` mode + a `thread_follow` flag.

| Mode | Behavior |
|------|----------|
| `off` | Ignore all (except an owner control command to re-enable). |
| `observe` | Record channel history, but **only reply when @-mentioned or when the thread already has an active bot session**. |
| `mention` | Reply only on @mention — **except** when `thread_follow=true` and this thread already has an active session (bot was @-ed earlier in it), then follow subsequent replies without re-@. |
| `review` | Never post visible text; only a thread status indicator (silent background work). |

**Default for a team channel:** `mention` + `thread_follow=true` — "@ to enter, then
follow the thread." This is the sweet spot for guarantee (1): quiet by default, not
awkward once engaged.

- **Borrowed from:** MeshClaw `slack/events.py:1816-1922` (`_route_message` activation
  gating) and the `thread_follow` / active-thread check (events.py:1898-1912).
- **SwarmAI lesson it satisfies:** discipline — **`observe` must record history from
  *authorized users only*.** MeshClaw learned this from a Shepherd security finding
  (`bdd39e84`, cited in events.py:1869-1875): recording unauthorized users' messages
  let non-owner content **poison the LLM context**. We adopt the same rule: an
  unauthorized user's text never enters any context buffer, not even "observed" history.

### Layer 2 — Identity (partially have; add fallback)

**Question:** who sent this, and what is their permission tier?

**Design:**
1. `sender_id = event["user"]` — most *user* messages carry their sender id
   (MeshClaw `_route_message`, events.py:1752). ⚠️ Some Slack event types
   (`bot_message`, `message_changed`, other subtypes) have **no** top-level
   `event["user"]` — when it is absent, **fail-closed to `unauthorized`** (no
   reply), never guess. (Ties to F1.)
2. Resolve a display name: cache → `get_user_info().real_name` → **fall back to the
   `name` field in the allowlist config** (MeshClaw events.py:1859-1867).
3. Map `sender_id` → **permission tier**: `owner (XG)` / `allowlisted non-owner` /
   `unauthorized`.
4. Pass the resolved name into the turn so the LLM uses the real identity, never a
   guess.

- **Borrowed from:** MeshClaw display-name resolution chain (events.py:1844-1867).
- **SwarmAI lesson it satisfies:** **COE02** — Slack's `users:read` scope is closed
  to us, so `get_user_info` may fail. The **allowlist-config `name` fallback is the
  fix**: identity resolution must not depend on a Slack scope we don't have. Also
  STEERING #6: the FIFO queue must never reveal *who* the bot is currently helping.
- **Per-user scoping:** the auth/activity window must be keyed on **(user, channel)**,
  never global — so one user's activity never extends another's grant in a shared
  channel (MeshClaw does this in `StatusReactionController`, handler.py:309-312).

### Layer 3 — Isolation (BUILD OURSELVES — the moat; see §4)

**Question:** given the sender's tier, which parts of XG's brain may this turn see?

This is the layer MeshClaw does not have (§2). It is specified in full in §4. In one
line: **two orthogonal lanes** — a **private lane** that is fail-closed excluded for
non-owners, and a **shared lane** that is actively injected so the bot is useful.

- **SwarmAI lesson it satisfies:** **C041** — fail-closed over fail-open; when the
  system cannot positively confirm a sender's tier or a section's classification, it
  **defaults to treating the sender as non-owner and the section as private**. Never
  the reverse.

### Layer 4 — Egress (borrow the streaming redactor)

**Question:** is the outgoing reply safe to send?

**Design:**
1. Redact every outbound message through `redact_credentials` + `redact_exfiltration_urls`.
2. If replies stream, use a **rolling-buffer redactor**: send only the confirmed-safe
   prefix, withhold the trailing (possible-partial-credential) run until the next
   chunk, and post the final message from the fully-redacted accumulated text.
3. Enforce STEERING #6 refusals here: refuse to take external actions as XG, refuse
   to hand over source code, refuse to emit raw personal data.

- **Borrowed from:** MeshClaw `StreamRedactor` rolling buffer (`slack/handler.py:2446`,
  `_append_stream` 2477-2502) — it exists specifically to stop a credential that is
  **split across streaming chunks** from reaching Slack unredacted.
- **SwarmAI lesson it satisfies:** **C041** (egress side) — the destructive/leaky
  action must be blocked structurally at the boundary, not left to model judgment.

---

## 4. The two-lane privacy model (the crux)

**Reframing the "太死" concern.** An early cut of this design used one blanket rule:
"non-owner → exclude EVOLUTION/PROJECTS/USER/MEMORY." XG correctly flagged that as
**too dead** — it strips *all* of XG's knowledge, and the bot's entire value is
carrying XG's domain knowledge to the team. But the fix is **not** to loosen the
private exclusions (that slides toward a C041 leak). The fix is to see that two
different things were conflated:

> **Protecting XG's private data** and **giving teammates something useful** are
> **orthogonal**, not two ends of one slider. So they must be two separate
> mechanisms — two lanes — not one graded exclusion list.

| | **Private lane** (fail-closed) | **Shared lane** (active-inject) |
|---|---|---|
| **Purpose** | Never leak XG's personal/self data | Make the bot *useful* to teammates |
| **Content** | USER.md (org chain, level, personal prefs), EVOLUTION.md (self-evolution, correction history), personal segments of MEMORY.md, any cross-user data | DDD project domain knowledge, `domain skills`, business knowledge XG **chooses** to let the team benefit from |
| **Default for non-owner** | **Excluded — structurally, always** | **Injected — this is the whole point** |
| **When classification is uncertain** | **Treat as private → exclude** (C041 fail-closed) | — |
| **Mechanism** | Code-path exclusion in context assembly, keyed on permission tier | Code-path inclusion of the shared corpus |

**Key insight:** a teammate's "useful" should come from the **shared lane** (DDD /
projects / skills), **never** from reading XG's MEMORY/EVOLUTION. Make the shared
lane rich, and you never need to loosen the private lane. The "太死" feeling comes
from *having only a private lane* — not from the private lane being too strict.

> **⚠️ Honest scope of the per-section fix (adversarial finding 2.2).** The
> shared lane's richness comes **primarily from DDD docs + domain skills**
> (whole-file shared — no per-section logic needed). Per-section classification of
> MEMORY/PROJECTS is a **bonus opt-in**, NOT the primary 太死 fix — because its
> default is fail-closed-private (below), an *untagged* MEMORY/PROJECTS section
> stays fully private. If we relied on per-section to carry usefulness, most mixed
> sections would default private and 太死 would silently return. So: usefulness =
> the shared corpus (DDD/skills); per-section = a way to *opt specific segments in*,
> not the load-bearing mechanism.

### Section-classification mechanism (adversarial finding 2.1 — this MUST be concrete, not hand-waved)

"Per-section" needs a **defined boundary + a deterministic, non-LLM classifier**
(C041: never leave a privacy decision to model judgment):

- **Section boundary** = a Markdown `##`/`###` heading block (heading + body until
  the next heading of equal-or-higher level).
- **Classification = an explicit affirmative tag scan (static, no LLM call):** a
  section is **shared ONLY if** it carries an explicit `<!-- lane: shared -->`
  marker on/under its heading. **Every untagged section → private** (fail-closed).
  A `<!-- lane: private -->` marker is redundant (private is the default) but
  allowed for readability.
- **Why affirmative-opt-in, not a denylist:** matches C041 (allowlist > denylist) —
  a newly-added section is private by default until someone deliberately shares it,
  so growth never silently leaks.
- **Whole-file files skip this entirely:** USER/EVOLUTION are whole-file private
  (never scanned); DDD/skills are whole-file shared. Only MEMORY/PROJECTS run the
  per-section tag scan.
- **Not an LLM classifier, not a regex on content** — a fixed tag scan, so the
  decision is auditable and reproducible.

### Per-file / per-section classification

| Source | Classification | Non-owner sees? | Rationale |
|--------|---------------|:---------------:|-----------|
| **USER.md** | Private | ❌ never | org chain / level / personal prefs — zero team value, catastrophic to leak |
| **EVOLUTION.md** | Private | ❌ never | self-evolution + correction history — internal, no team value |
| **MEMORY.md** | **Per-section** | partial | personal/correction segments = private; a project-status segment *may* be shared. **Classify by section, not whole-file.** Unclassifiable section → private (fail-closed). |
| **PROJECTS.md** | **Per-section** | partial | project domain context = shareable; anything tagging XG-personal = private. Per-section, not whole-file. |
| **DDD docs** (PRODUCT/TECH/IMPROVEMENT/PROJECT per project) | Shared | ✅ (the point) | domain knowledge — this is what makes the bot useful to the team |
| **domain skills** | Shared | ✅ | e.g. business-analysis skills XG built for the team |
| **SOUL / AGENT / SWARMAI / IDENTITY** | Neutral | ✅ (behavior, not data) | governs behavior, carries no private data |

> **Whole-file vs per-section.** USER and EVOLUTION are whole-file private (locking
> them fully is correct — zero team value). MEMORY and PROJECTS are **per-section** —
> this is exactly where "太死" gets fixed: their project/domain segments belong in the
> shared lane; only their personal/correction segments stay private.

### Relation to the existing context-assembly exclusion matrix

**✅ VERIFIED (2026-07-06, live code).** Phase 0 confirmed all three points that were
previously beliefs:
- **(a) the matrix exists as code** and is **wired on the live path** —
  `prompt_builder.py:670-683` branches on `session_type` and excludes files; it is
  NOT dead code.
- **(b) the tier discriminator is really populated** — `channel_context.is_group` /
  `is_owner` are set (`channels/base.py:57,64`), so the branch actually fires per
  session type; and sender tier is a **3-tier model** (OWNER/TRUSTED/PUBLIC) resolved
  at a single point (`gateway.py:338`, `system_prompt.py:110`).
- **(c) file access is a REAL structural gate, not prose** — see §6(c): a non-owner
  channel session is force-sandboxed to `channel_files/<sender_id>/` via
  `create_file_access_permission_handler` (`prompt_builder.py:1334-1344`,
  `security_hooks.py:1510`), which **denies at the hook layer before the tool runs**.

So the matrix **is** the private lane's coarse ancestor (confirmed). This design's
refinement over the verified baseline is unchanged: (a) add the **shared lane** as a
first-class, actively-injected corpus (the matrix only *excludes*, never
*includes-for-usefulness*); (b) move MEMORY/PROJECTS from whole-file exclusion to
**per-section** classification (mechanism above). Both remain **net-new work** — the
baseline gives us coarse whole-file exclusion, not the per-section shared lane.

---

## 5. Failure-mode catalog (privacy-leak paths + the layer that catches each)

| # | Leak path | Catching layer | Mechanism |
|---|-----------|:--------------:|-----------|
| F1 | **Misidentified sender** — an unauthorized user treated as allowlisted (or a non-owner as owner) | **L2 (load-bearing)** | Tier resolution is fail-closed: unknown/unresolvable/absent sender → `unauthorized` (no reply) or lowest tier; **never** default to owner. ⚠️ L3 **cannot** compensate for an L2 tier error — it faithfully serves whatever tier L2 hands it, so a wrong tier here leaks. L2 fail-closed is the only defense. |
| F2 | **Private section misclassified as shared** — a MEMORY/PROJECTS section carrying personal data slips into the shared lane | L3 | Affirmative-tag classifier (§4): a section is shared ONLY with an explicit `<!-- lane: shared -->` tag; untagged → private (C041 fail-closed). No LLM, no content-regex — a static tag scan. |
| F3 | **Cross-chunk credential leak** — a secret split across two streaming chunks each individually passes redaction | L4 | Rolling-buffer redactor withholds the trailing partial-credential run until confirmed safe (MeshClaw StreamRedactor). |
| F4 | **Shared lane accessed by an unauthorized user** — someone not on the allowlist reaches shared-lane content | L1 → L2 | Unauthorized senders are rejected at activation (ephemeral) and never reach L3 at all; shared lane is only assembled after tier ≥ allowlisted. |
| F5 | **Observed-history poisoning** — an unauthorized user's message enters the context buffer via `observe` mode | L1 | `observe` records **authorized users only** (Shepherd bdd39e84 discipline). |
| F6 | **Owner-only control leaking via a channel** — a dashboard link / owner action surfaced in-channel | L4 | Owner-scoped links go by **DM only, never channel** (MeshClaw allowlist.py); owner control commands are tier-gated. |
| F7 | **FIFO queue reveals who's being helped** — bot discloses the identity/content of another user's in-flight request | L2 | Queue is opaque; never reveal who the bot is currently helping (STEERING #6). |
| F8 | **Introspection / self-summarization** — a non-owner asks "summarize your USER.md / what corrections have you learned / quote your system prompt" | **L3 (structural, NOT L4)** | Private-lane files are **excluded at context ASSEMBLY** for non-owner turns → the data is **not in the turn's context**, so it cannot be summarized/quoted/leaked by any prompt. This is why L3 assembly-exclusion (structural) is the real defense, NOT L4's "refuse to answer" (which is model-judgment and forbidden by §1 as the *sole* guard). If it isn't loaded, it can't be spoken. |
| F9 | **Cross-user context bleed** — user A's conversation history/context injected into user B's turn | L2 → L3 | Per-`(user, channel)` keyed context/session stores (§3 L2); B's turn assembles only B's history. A shared or global history buffer would leak A→B — stores must be user-keyed. |

---

## 6. Phase 0 — verification RESULTS (RUN 2026-07-06, live code)

Phase 0 was **executed this session** against live `backend/channels/` + `backend/core/`
code (not the system prompt, not KNOWLEDGE — the running code, per R16b). Each item
below carries the observed verdict + file:line evidence. The grep commands used are
retained at the end for reproducibility.

| # | Check | Verdict | Evidence (live code) |
|---|-------|:-------:|----------------------|
| **(a)** | Session-type exclusion matrix enforced on the live path? | ✅ **PASS** | `prompt_builder.py:670-683` branches on `session_type`, excludes files; NOT dead code. Discriminator `is_group`/`is_owner` really populated (`channels/base.py:57,64`). |
| **(b)** | Every inbound message carries sender identity + tier? | ✅ **PASS** | `gateway.py:338` sets `permission_tier` per message; 3-tier OWNER/TRUSTED/PUBLIC, single resolution point (`system_prompt.py:110`). No conflation across senders. |
| **(c)** | File access to XG's brain structurally blocked for non-owner? | ✅ **PASS (stronger than expected)** | Non-owner channel session force-sandboxed to `channel_files/<sender_id>/` (`prompt_builder.py:1334-1344`). Handler `security_hooks.py:1510` normalizes+symlink-resolves paths and **denies before tool runs** — covers Read/Write/Edit/Glob/Grep **and Bash** (regex for `cat/head/>` etc.). Even `Read ~/.swarm-ai/SwarmWS/MEMORY.md` → deny. **This makes F8's "system enforced" claim TRUE.** |
| **(c′)** | Outbound *content* redaction (credentials/exfil URLs)? | 🔴 **FAIL → gap G1 (§9)** | `redact_credentials`/`redact_exfiltration_urls` **do not exist in our backend** — they were MeshClaw functions I cited as borrow candidates. `channels/` only has `_sanitize_filename` (filenames, not message text). **P1** (not P0 — file-sandbox already blocks reading XG's private files; residual = agent-generated text + MCP return values). |
| **(c″)** | MCP tools scoped by *sender* permission tier? | 🔴 **FAIL → gap G2 (§9)** | `mcp_config_loader.py:522` gates MCP by **channel tier** (always/channel/ondemand), **NOT by sender's permission_tier**. Same channel → TRUSTED and PUBLIC get the same MCP set. Only `system_prompt.py` *behavioral* text restricts them — **not a structural gate**. **P1, escalates to P0 if a channel binds a sensitive MCP** (sentral revenue / outlook). |
| **(d)** | Display-name fallback for missing `users:read` (COE02)? | ⚠️ verify at build | grep pending in impl phase — allowlist-config `name` fallback is the design target (§3 L2). |
| **(e)** | Activation-mode concept, or reply-to-all today? | ⚪ net-new (expected) | No `activation`/`thread_follow` concept found in `channels/` — L1 is net-new work, **not a P0** (it's a feature add, not a hole). |

**Headline:** the **foundation holds** — (a)(b)(c) are all real structural defenses,
so the design is buildable on solid ground. The two 🔴 are **egress-layer and MCP-layer
narrowing gaps**, not "matrix didn't land." Both fold into **Layer 4 / §9**.

<details>
<summary>Reproducibility — grep commands used (grep-hit ≠ enforced; each was call-traced to the live path)</summary>

```bash
# (a) exclusion matrix on live path
grep -rn -iE "session_type|group_channel|non_?owner|exclud" backend/core/prompt_builder.py backend/channels/ | grep -iE "memory|user|evolution|projects"
# (b) sender identity + tier
grep -rn -iE "permission_tier|TRUSTED|PUBLIC|sender" backend/channels/gateway.py backend/core/system_prompt.py
# (c) file-access gate definition + wiring
grep -rn -iE "create_file_access_permission_handler|channel_files" backend/core/prompt_builder.py backend/core/security_hooks.py
# (c′) egress redaction
grep -rln -iE "def redact_credentials|def redact_exfiltrat" backend/
# (c″) MCP per-sender-tier gating
grep -n -iE "permission_tier|sender|tier|channel" backend/core/mcp_config_loader.py
```
</details>

> **⚠️ grep-hit ≠ enforced (the rule I followed).** Every PASS above required
> call-tracing the match to the live inbound-channel path — not just that the string
> exists. (a)/(c) were confirmed by reading the actual branch + handler body, not the
> grep hit alone.

---

## 7. Borrow from MeshClaw vs Build ourselves

| Capability | Source | Why |
|-----------|--------|-----|
| Activation modes (off/observe/mention/review) + `thread_follow` | 🟢 **Borrow** (events.py:1816-1922) | Mature, exactly our "@ to enter, follow thread" need (guarantee 1) |
| `observe` records authorized users only | 🟢 **Borrow the discipline** (Shepherd bdd39e84) | Prevents context poisoning — a real finding they paid for |
| display_name fallback from allowlist config | 🟢 **Borrow** (events.py:1859-1867) | Directly fixes our COE02 (`users:read` closed) |
| Rolling-buffer streaming redactor | 🟢 **Borrow** (handler.py:2446) | Fixes cross-chunk credential leak (F3); we'd hit the same bug |
| (user, channel)-scoped auth window | 🟢 **Borrow** (handler.py:309-312) | Correct per-user scoping in shared channels |
| DM-only owner links, never in-channel | 🟢 **Borrow** (allowlist.py) | Prevents F6 |
| **Two-lane private/shared isolation for multi-user** | ⚪ **Build ourselves** | MeshClaw is single-owner (events.py:634) — it has NO cross-user isolation to copy. This is our moat and our hardest requirement. |
| **Per-section MEMORY/PROJECTS classification** | ⚪ **Build ourselves** | No MeshClaw analogue; the fix for "太死" |
| **Fail-closed tier + classification defaults** | ⚪ **Build ourselves** (from C041) | Our earned lesson, not in their denylist-based model |

---

## 8. Non-goals

- **Not** building multi-platform (Feishu/Teams) abstraction now — STEERING #6 is
  Slack-specific; YAGNI, add later if needed (deletion-friendly).
- **Not** copying MeshClaw's trust/YOLO auto-approve tiers — our privacy bar (acting
  as XG for others) forbids YOLO.
- **Not** implementing anything in this run — this is docs-only. Phase 0 verification
  is **done** (§6); implementation (§9 gaps, then net-new features) is the next,
  separate step, sequenced in §9.
- **Not** loosening the private lane to improve usefulness — usefulness comes from
  enriching the shared lane (§4), never from weakening exclusions.

---

## 9. Real gaps found by Phase 0 — remediation plan

Phase 0 turned "belief" into "two concrete gaps." Both are **narrowing** gaps at the
outer layers, not foundation holes. Priority is honest: neither is P0 *today* because
the file-sandbox (§6c) already blocks the catastrophic path (reading XG's private
files); they become P0 only under the escalation trigger noted.

### G1 — No egress content redaction (Layer 4)

- **What:** outbound channel text is not scanned for credentials / exfiltration URLs.
  `redact_credentials`/`redact_exfiltration_urls` **do not exist** — I mis-cited them
  as ours; they are MeshClaw's.
- **Residual threat (why P1 not P0):** non-owners already **cannot read** XG's
  MEMORY/USER via the file-sandbox. What can still leak = secrets appearing in
  **agent-generated text** or **MCP tool return values** (e.g. an AWS key echoed from
  a tool result), split-chunk or whole.
- **Fix:** build a `redact_credentials` + `redact_exfiltration_urls` egress filter,
  applied at the channel send boundary; for streaming, the **rolling-buffer** variant
  (§4 L4 / MeshClaw `handler.py:2446`) to catch cross-chunk secrets (F3). Structural
  at the boundary — not model judgment (C041 egress).
- **Scope:** bugfix pipeline, ~1 module + wire into `channels/` send path.

### G2 — MCP tools not scoped by sender permission tier (structural gap)

- **What:** `mcp_config_loader.py:522` filters MCP by **channel tier**, never by the
  **sender's** OWNER/TRUSTED/PUBLIC tier. Two users in the same channel get the same
  MCP set; only `system_prompt.py` *behavioral prose* tells a PUBLIC user "don't."
  That is exactly the "ask the LLM nicely" anti-pattern §1 forbids as a *sole* guard.
- **Escalation trigger (P1→P0):** if any live channel binds a **sensitive MCP**
  (sentral = revenue, outlook = email), a non-owner sender is structurally one
  behavioral-refusal away from reaching it. **Must grep live channel configs to grade
  actual severity** (next step — see below).
- **Fix:** add a **sender-tier → MCP allowlist** intersection in `build_mcp_config`,
  so a non-owner turn structurally receives only tier-permitted MCP tools
  (fail-closed: unknown tier → minimal set). Mirrors the file-sandbox pattern that
  already works for file tools.
- **Scope:** bugfix pipeline, localized to `mcp_config_loader` / `prompt_builder`
  MCP-assembly, keyed on the tier already resolved at `gateway.py:338`.

### Sequencing

1. **First (cheap, decisive):** `grep` live channel configs for which MCPs are bound →
   grade G2 as P1 vs P0. One command, changes the priority.
2. **G2** if any sensitive MCP is channel-bound (structural gate, mirrors §6c).
3. **G1** egress redaction (independently valuable; also needed before L4 ships).
4. **Then** the net-new features (L1 activation, per-section shared lane §4) on the
   now-verified + gap-closed baseline.

---

## Appendix — MeshClaw source references (read 2026-07-06, mainline)

- `src/mesh_claw/slack/events.py:634` — single-owner invariant (`_allowed_users` owner-only)
- `src/mesh_claw/slack/events.py:1744` — `_route_message` (activation + identity + dedup)
- `src/mesh_claw/slack/events.py:1816-1922` — activation-mode gating + `thread_follow`
- `src/mesh_claw/slack/events.py:1844-1867` — display-name resolution + allowlist fallback
- `src/mesh_claw/slack/events.py:1869-1875` — observe-records-authorized-only (Shepherd bdd39e84)
- `src/mesh_claw/slack/handler.py:287` — `StatusReactionController` ((user,channel)-scoped window at 309-312)
- `src/mesh_claw/slack/handler.py:2446,2477` — `StreamRedactor` rolling-buffer egress
- `src/mesh_claw/slack/allowlist.py` — owner-approval flow, DM-only dashboard links

_SwarmAI governance anchors: STEERING #6 (Slack persona), C041 (fail-closed / irreversible-op), COE02 (users:read closed), Shepherd-finding-bdd39e84 (observe poisoning)._
