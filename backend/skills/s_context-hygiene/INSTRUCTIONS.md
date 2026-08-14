# Context Hygiene — Clean & Compress the 12 Context Files

The methodology for cleaning / 瘦身 SwarmAI's context files, so the next session
doesn't re-derive "which source file, edit where, cut what." Codified from the
2026-08-11 session that compressed all 12.

> **What this is NOT (scope-away — read first, avoid the C042 trap):**
> - **NOT `context_health_hook`** — that hook runs every session and does AUTONOMOUS
>   age-decay + dedup + regen (MEMORY/KNOWLEDGE/EVOLUTION lifecycle, PROJECTS + KNOWLEDGE-Index
>   regeneration). It does NOT do semantic compression — proof: echoed-titles + dated-pointer
>   fragments + drift numbers survive it for months. This skill is the MANUAL SEMANTIC sweep
>   the hook cannot do. **Never rebuild or duplicate the hook's autonomous cleanup.**
> - **NOT `s_persist`** — that routes NEW knowledge by content-type to the right section.
>   This skill cleans what's ALREADY there and knows which SOURCE FILE to edit + whether a
>   rebuild is needed. Orthogonal.
> - **NOT a scheduled job** — cleaning a curated cognitive store is a READ-and-JUDGE job for
>   the agent (see §3), NOT a batch LLM-judge pass. Do not make this autonomous.

---

## 1. Source-Routing Table — WHERE to edit + does it need a rebuild

**⚠️ Do NOT trust a frozen copy of this table — DERIVE IT LIVE.** The authoritative
routing is the `CONTEXT_FILES` list in `backend/core/context_directory_loader.py`
(the `user_customized` field drives it). Re-verify against source every time (R30#1):

```bash
python <skill>/scripts/scan.py --routing            # prints the live table
python <skill>/scripts/scan.py --routing --file MEMORY.md
```

The three classes (read out of `user_customized` + the auto-gen set):

| Class | Files | Source of truth | Edit where → effect |
|-------|-------|-----------------|---------------------|
| **System** (`user_customized=False`) | SWARMAI · IDENTITY · SOUL · AGENT | `backend/context/<F>.md` | Startup **always-overwrites** `.context/` from source + `chmod 0444`. **Edit the SOURCE in `backend/context/`, then BUILD + RESTART.** Editing `.context/` is silently overwritten. |
| **Runtime** (`user_customized=True`) | SELF · USER · STEERING · TOOLS · MEMORY · EVOLUTION | `.context/<F>.md` (copy-if-missing) | The `.context/` copy IS the real store. **Edit `.context/` directly — instant effect next session, no build.** |
| **Auto-generated** | PROJECTS (whole file) · KNOWLEDGE (bottom *Index* section only) | regenerated on startup / by `context_health_hook` | **Never hand-edit the auto part** — it's rewritten. KNOWLEDGE's body ABOVE the index IS agent-authored and editable. |

**Discriminator code (cite, re-verify — do not memorize a frozen list):**
- `backend/core/context_directory_loader.py` seeding block (~L490-526): `spec.user_customized`
  True → copy-if-missing + `chmod 0644`; False → always-overwrite + `chmod 0444`.
- `backend/core/context_brain.py` tier map: `system` / `auto`.
- `context_health_hook` autonomous cleanup (decay/dedup/archive). (The in-prompt PROJECTS.md index + KNOWLEDGE "## Knowledge Index" auto-nav were removed 2026-08-14 — no longer auto-generated.)

**Gotchas that bit me:**
- `SELF.md` is `user_customized=True` (runtime-owned, edit `.context/`) even though it
  reads like a system file — but its own header says human/distill-only; don't auto-cultivate it.
- A system-file edit is NOT live until rebuild+restart (binary mtime > source mtime). Restart is
  destructive → **needs XG approval** (AGENT R12).

---

## 2. Cleaning Rules — WHAT to cut

Cut these (each is drift or noise, git-recoverable):

1. **Drift numbers (AGENT R30#4)** — volatile, decision-inert figures: LOC / file / test
   counts, sizes (KB/MB/GB), token counts, %-utilization, star snapshots, "N skills", line
   numbers in prose, commit hashes as data. Stale → they mislead; fresh → upkeep for nothing.
   **Fix:** replace with the *reproducible method* (`run git ls-files | …`) or a qualitative
   fact ("runs in production daily"), OR delete. Never store the frozen output.
2. **CJK-in-system-prose** — in the 4 always-injected SYSTEM files (SWARMAI/IDENTITY/SOUL/AGENT),
   prefer English for cognition text. **KEEP:** XG's verbatim quotes (they're evidence),
   detection-term lists, and direct-mode triggers ("直接做" / "just do it") — those are
   load-bearing, not decoration.
3. **Echoed titles** — `- [type] **T** — T — body` where the body repeats the title verbatim.
   Collapse to `- [type] **T** — body`.
4. **Dated pointer-only fragments** — one-line `- YYYY-MM-DD: <fragment>` entries that only
   point at a DailyActivity file. The DailyActivity IS the record; the pointer (often a
   truncated mid-sentence residue) is drift. Delete the pointer, keep the curated `[decision]`.
5. **Blow-by-blow incident re-tellings → pattern + tell.** A correction that narrates the whole
   incident play-by-play compresses to its **pattern** (what class of error) + **durable tell**
   (the reusable reflex). Cut the narration, keep the judgment.
6. **Historical reframe / before-after narration** once the principle is already sedimented into
   SOUL/AGENT — the "we used to do X, now we do Y" table is history; the live principle is enough.

`scan.py` surfaces classes 1/3/4 mechanically; classes 2/5/6 are pure human judgment.
**Scanner's drift-number detector is deliberately conservative** — it flags sized/counted
figures (LOC/tests/KB/MB/%/`~NK`) but NOT bare "line 4521" / commit hashes / "45000 tokens".
It is a lead-surfacer, not exhaustive: treat a clean scan as "no *obvious* drift," then still
read for the subtler forms in Rule 1 by hand. (Under-catching is safe here — you judge every hit.)

---

## 3. The Method — mechanical surface, then READ-and-JUDGE

**Cleaning a curated store is a READ-and-JUDGE job for the agent, NOT a batch auto pass**
(sedimented MEMORY principle). The scanner is a *candidate surfacer*, not a decider:

```
1. SCAN   python <skill>/scripts/scan.py --root .context [--file F] [--json]
          → a candidate list (echoed-title / drift-number / dated-pointer + line #s)
2. READ   open the file, read each flagged region IN CONTEXT
3. JUDGE  for each candidate ask: is this genuinely noise/drift, or load-bearing?
          (a "2MB data:-URL limit" figure in a lesson is a domain FACT, not drift —
           the scanner can't tell; you can. False-positives are EXPECTED and fine.)
4. DELETE on sight, in-place, no archive — git history is the recovery net
          (CLEANUP-TIMIDITY tell: archiving noise just relocates the graveyard).
5. For classes 2/5/6 (CJK / blow-by-blow / historical) there's no scanner — read
   the section and compress by hand, diffing by SECTION (see §4).
```

Route every edit through §1 (system file → edit source + flag rebuild; runtime → edit
`.context/` direct; auto-gen → don't touch the auto part).

---

## 4. 🔴 THE RED LINE — never gut judgment (C046 guard)

Compression must cut NARRATION, never JUDGMENT. Non-negotiable:

- **NEVER cut a correction's `pattern` + `durable tell`** — that pair IS the reusable
  judgment; the incident narration around it is what compresses.
- **NEVER cut a principle/rule kernel** — the imperative + its one-line why.
- **Diff by SECTION, not by line count.** Every philosophy/pattern section must either
  map to the compressed version OR be re-homed with a documented reason. A large
  line-count drop is itself the SIGNATURE of the gut-and-summarize bug — re-verify it,
  don't celebrate it. (C046: 206→44 line drop = the tell I'd deleted the value.)
- The goal is "same judgment, fewer tokens" — if a future session couldn't make the
  same decision from the compressed text, you cut too much.

---

## 5. Boundaries

**Always:** derive routing live (§1); read-and-judge each candidate (§3); preserve
pattern+tell / principle kernel (§4); git is the recovery net (delete noise, don't archive).

**Ask first:** touching a SYSTEM file (needs build+restart — get XG approval, AGENT R12);
deleting anything borderline (not clearly noise/drift).

**Never:** auto-fix / batch-delete (scan.py is read-only by iron law); rebuild
`context_health_hook`'s autonomous cleanup (C042); cut a pattern+tell or principle kernel (C046); the C041
irreversible-destructive-op gate still applies (this is about noise in git-tracked
files — recoverable; it does NOT relax the gate on repo-visibility/force-push/deleting
non-gitignored user data).

---

## 6. Scanner reference

`scripts/scan.py` — read-only. `--routing` (live table) · `--root DIR` · `--file NAME` ·
`--detector echoed-title,drift-number,dated-pointer` · `--json`. It has NO `--fix`/`--apply`
mode and MUST never grow one (§3 + §4). Every hit is a lead for human judgment, not a verdict.
