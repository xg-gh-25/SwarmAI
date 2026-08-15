# Context Hygiene — Clean & Compress the Context Files

The methodology for cleaning / 瘦身 SwarmAI's context files, so the next session
doesn't re-derive "which source file, edit where, cut what." Codified from the
2026-08-11 session that compressed them all.

Two modes: **manual** (§1-§6 — a human-directed semantic sweep of a named file) and
**sweep** (§7 — a weekly autonomous sweep across ALL cognitive stores with an
adversarial delete-gate). Both share the same cleaning rules (§2) and red line (§4).

> **What this is NOT (scope-away — read first, avoid the C042 trap):**
> - **NOT `context_health_hook`** — that hook runs every session and does AUTONOMOUS
>   age-decay + dedup + regen (MEMORY/KNOWLEDGE/EVOLUTION lifecycle, KNOWLEDGE-Index
>   regeneration). It does NOT do semantic compression — proof: echoed-titles + dated-pointer
>   fragments + drift numbers survive it for months. This skill is the SEMANTIC sweep
>   the hook cannot do. **Never rebuild or duplicate the hook's autonomous cleanup.**
> - **NOT `s_persist`** — that routes NEW knowledge by content-type to the right section.
>   This skill cleans what's ALREADY there and knows which SOURCE FILE to edit + whether a
>   rebuild is needed. Orthogonal.
> - **NOT an UNGATED batch LLM-judge pass.** The原-2026-08-11 rule said "never make this a
>   scheduled job" — because a naive batch judge over a curated store finds ~0 and mis-deletes
>   load-bearing content (C046). The §7 sweep is allowed ONLY because it does not delete on a
>   single judge's say-so: every delete-candidate must survive an ADVERSARIAL review (a skeptic
>   role that must fail to justify "load-bearing" before the delete lands), and git + a weekly
>   audit report are the recovery net. Autonomy is earned by the gate, not by the schedule.

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

---

## 7. Sweep Mode — the weekly autonomous Darwinian-forgetting sweep

**Philosophy (XG, 2026-08-15): a cognitive store that only grows violates Darwin —
forgetting (deletion) is a design FEATURE, not a risk.** `value, not age, decides
survival`; the danger is never "we deleted" but "we deleted a load-bearing entry with
nobody to challenge it" (C046). So the sweep deletes freely — but every delete must
first survive an ADVERSARIAL challenge. The gate, not a human-in-the-loop, is what makes
autonomy safe.

**Runs headless on Sonnet** (`agent_task` hardcodes `--model sonnet`). A weaker judge is
acceptable *because* the delete-gate defaults to KEEP — a Sonnet skeptic errs toward
retention, git is the recovery net, and the weekly report is the audit trail.

### 7.1 Two job groups — DIFFERENT delete criteria (never one criterion for both)

| Group | Stores | Delete criterion (candidate-selection) |
|-------|--------|------------------------------------------|
| **Job 1 — live cognitive stores** | `.context/KNOWLEDGE.md`, `MEMORY.md`, `EVOLUTION.md`, every `Projects/*/` DDD doc (PRODUCT/TECH/IMPROVEMENT/PROJECT) | CONSERVATIVE — dedupe (semantic near-dupes), de-redundancy (cross-section repeats), de-stale (contradicts live source), + entries at `decay:archived` **and** `ref:0` **and** `last:` long-idle. Bias: when unsure, KEEP. |
| **Job 2 — archive cold-store** | `.context/*-archive*.md`, `Knowledge/Archives/` | AGGRESSIVE — cross-month duplicates, entries already re-absorbed into a live store, and content the manual read judges genuinely spent. Bias: cold-store is already retired, lean DELETE. |

Why split: live-store criterion asks "is this still load-bearing?" (keep-biased);
archive criterion asks "does this cold copy still earn recall?" (delete-biased). One
criterion across both either mis-deletes live load-bearing content or hoards archive junk.

⚠️ **No `recall-never-hit` criterion exists** — recall is a pure READ path and bumps no
counter (`ref:N` = times mentioned in prose via `bump_references`, NOT recall hits). Do
NOT select-for-delete on "recall never used it"; that signal is not measured. Use only
`ref_count` / `last_referenced` / `decay_state` + semantic reading.

### 7.2 The sweep procedure (single-agent role-switch — SOUL standing decision)

Do NOT spawn a sub-agent (headless tool-allowlist can't guarantee Task). Instead ONE
agent switches roles, per store:

```
1. JUDGE role  — read the store. Produce a DELETE-CANDIDATE list, GROUPED by
   store/section (group granularity — XG decision 2026-08-15, not per-item: per-item
   adversarial is too costly and turns a weekly job into a burden; grouping still closes
   the "deleted with nobody challenging" risk). Each group = {store, section, candidates[], one-line why-noise each}.
2. SKEPTIC role — for EACH group, RE-READ only the candidates with a fresh adversarial
   framing: "prove each of these is load-bearing; DEFAULT TO KEEP if you can name any
   reader/decision it still serves." Output per candidate: DELETE (skeptic could not
   justify) | KEEP (skeptic named a live value).
2.5 SNAPSHOT   — 🔴 MANDATORY BEFORE ANY DELETE. The cognitive stores are ALL gitignored
   (KNOWLEDGE/MEMORY/EVOLUTION/archive) → git CANNOT restore them; a git commit is NOT a
   recovery net for these files. So before editing, copy each about-to-be-edited file
   verbatim to `.context/.sweep-backups/{{date}}/<relative-path>` (full file, not a diff —
   restore = copy back). This physical snapshot IS the only recovery net. If the snapshot
   copy fails for a file, SKIP deleting from that file this run (no net → no delete).
   Then prune `.context/.sweep-backups/` to the most recent 8 dated dirs (this backup dir
   must not become its own graveyard — Darwinian rule applies to the net too).
3. APPLY        — delete only DELETE-verdict lines, in place. For git-TRACKED swept files
   only (e.g. a DDD doc under Projects/SwarmAI/), git add ONLY those specific files (never
   `-A`; a parallel session may hold other changes — R29) and commit. Gitignored stores
   need no commit — the §2.5 snapshot is their net. Commit message lists counts per store.
4. REPORT       — write Knowledge/JobResults/{{date}}-context-sweep.md: per store —
   candidates found, deleted, KEPT-by-skeptic (with the value the skeptic named), the
   snapshot dir path, and (for tracked files) the commit hash. This report is the AUDIT
   trail, not an approval gate.
```

### 7.3 Red lines inside the sweep (inherit §4, enforced in the SKEPTIC pass)

The skeptic treats these as **presumed load-bearing — KEEP unless overwhelmingly proven dead**:
- a correction's `pattern` + `durable tell`; a principle/rule kernel (§4)
- `MEMORY_EVERGREEN` / `KNOWLEDGE_EVERGREEN` sections; DDD evergreen-guarded entries
- XG-curated prose (Open-Threads, hand-written decisions) — `source:manual`, not `source:auto`
- a "wrong on purpose" example (R30#4 counter-example, e.g. a stale number kept AS the lesson)
- SYSTEM files (SWARMAI/IDENTITY/SOUL/AGENT) are OUT OF SCOPE for the sweep entirely —
  they need build+restart and human sign-off (AGENT R12). Sweep touches runtime + DDD + archive ONLY.

### 7.4 Boundaries specific to sweep

**Always:** group-level adversarial before any delete; **physical snapshot (§2.5) before
any delete — it is the ONLY recovery net for the gitignored stores, git is not**; weekly
report as audit. **Never:** delete on the JUDGE pass alone (skeptic is mandatory); delete
from a file whose snapshot copy failed; `git add -A`; touch a SYSTEM file; select-for-delete
on unmeasured signals (recall hits); let `.sweep-backups/` grow unbounded (keep last 8).
