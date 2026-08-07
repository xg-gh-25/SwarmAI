/**
 * dddOverview — pure data helpers for the Brain-detail Overview tab (run_6c68088f).
 *
 * The Overview tab (in BrainHub.tsx) answers "what state is this brain in / what
 * should I do". These two PURE functions derive its two data-backed signals from
 * data ALREADY loaded (getBrainDetail + getReview) — no new backend, no React.
 * Extracted so the mapping logic is mutation-testable (GUI33 extract-intent-to-
 * pure-helper); the component is a thin renderer over these.
 *
 *   docSignalMap(members, review)  → per-canonical-doc {newCount, pendingCount}
 *   weeklyReportModel(detail, review) → current-DDD-only weekly summary model
 *
 * ── Gate-1 correctness contracts (do NOT regress — each has a pinned test) ──
 * F2a  Hunk→doc match is by BASENAME, and EXCLUDES the 2-understanding/knowledge/
 *      recall CORPUS (a corpus file can share a canonical basename, e.g.
 *      .../knowledge/designs/PRODUCT.md — it is NOT the canonical PRODUCT.md).
 *      Mirrors the backend _tag_hunk basename check (ddd_brain.py:985) but adds the
 *      corpus exclusion the frontend needs. `member.path` may be the migrated
 *      '2-understanding/TECH.md' OR the un-migrated bare 'TECH.md' (strangler
 *      fallback) — basename keying handles both.
 * F2b  `proposal.target_doc` is a BARE filename ('TECH.md'), never a path — match
 *      it against basename(member.path), never full-path equality.
 * F4   weeklyReportModel emits a trust DISTRIBUTION (count of section-trust levels),
 *      NEVER a single collapsed trustPct. The backend Gate-1 explicitly refused to
 *      invent a project-level trust rollup (ddd_brain.py:495 "no project-composite";
 *      ddd.ts:46 "deliberately NO project-level trust rollup") — re-adding one in the
 *      UI layer would re-litigate that settled refusal. Report the distribution only.
 */
import type { BrainDetail, ReviewData, SectionMember } from '../../services/ddd';

/** Per-doc signal for a core-doc card. Both counts are "since last review"
 *  (the getReview watermark window), NOT a calendar week. */
export interface DocSignal {
  /** auto-applied hunks touching this doc since the last review (watermark). */
  newCount: number;
  /** pending proposals targeting this doc awaiting a human decision. */
  pendingCount: number;
}

/** basename of a project-relative member path ('2-understanding/TECH.md' → 'TECH.md';
 *  bare 'TECH.md' → 'TECH.md'). */
function basename(p: string): string {
  return p.split('/').pop() ?? p;
}

/**
 * Does a review hunk's file belong to the canonical doc `member`?
 *
 * A hunk.file is workspace-relative, e.g. 'Projects/SwarmAI/2-understanding/TECH.md'
 * (git diff pathspec, '+++ b/' stripped — ddd_brain.py:965). The canonical doc lives
 * DIRECTLY under 2-understanding/ (or at the project root in the un-migrated layout).
 *
 * Match = basenames equal AND the hunk is NOT inside the recall corpus
 * (2-understanding/knowledge/…), which can carry a file of the same basename
 * (e.g. .../knowledge/designs/PRODUCT.md) that is NOT the canonical doc (F2a).
 */
function hunkMatchesDoc(hunkFile: string, member: SectionMember): boolean {
  if (hunkFile.includes('/knowledge/')) return false;   // F2a: recall-corpus, never canonical
  return basename(hunkFile) === basename(member.path);
}

/**
 * Map each canonical ② doc member to its {newCount, pendingCount} signal.
 *
 * Returns a Map keyed by the FULL member.path (the stable card identity), so the
 * caller can look up a signal per card. EVERY passed member gets an entry (zero
 * counts when no signal) — the Overview shows a fixed set of doc cards regardless
 * of activity (XG: fixed layout, no dynamic reorder).
 *
 * Pure: no side effects, null-safe on a not-yet-loaded review.
 */
export function docSignalMap(
  members: SectionMember[],
  review: ReviewData | null,
): Map<string, DocSignal> {
  const out = new Map<string, DocSignal>();
  for (const m of members) out.set(m.path, { newCount: 0, pendingCount: 0 });
  if (!review) return out;

  for (const h of review.hunks) {
    // Only auto-applied cultivation hunks count as "new since review" content;
    // risky·staged hunks are surfaced as pending via the proposals list instead.
    if (h.tag !== 'cultivation·auto-applied') continue;
    for (const m of members) {
      if (hunkMatchesDoc(h.file, m)) {
        out.get(m.path)!.newCount += 1;
        break;   // a hunk belongs to at most one canonical doc
      }
    }
  }

  for (const p of review.proposals) {
    // F2b: target_doc is a bare filename → match against basename(member.path).
    for (const m of members) {
      if (basename(m.path) === p.target_doc) {
        out.get(m.path)!.pendingCount += 1;
        break;
      }
    }
  }

  return out;
}

// ── The 4 trust levels the backend section_health emits, in severity order. ──
const TRUST_LEVELS = ['full', 'high', 'moderate', 'low'] as const;
type TrustLevel = (typeof TRUST_LEVELS)[number];

/** Trust DISTRIBUTION — count of section-level trust levels across all docs, plus
 *  `unscored` for sections with a null/absent level. NOT a collapsed rollup (F4). */
export interface TrustDistribution {
  full: number;
  high: number;
  moderate: number;
  low: number;
  unscored: number;
}

/** The current-DDD-only weekly-report model (AC7). Everything is derived from data
 *  already loaded — no global one-pot, no file written. */
export interface WeeklyReportModel {
  /** auto-applied hunks across ALL canonical docs since the last review. */
  autoApplied: number;
  /** pending proposals across the brain (the escalation queue). */
  pending: number;
  /** basenames of the core docs with any signal (new or pending) — "what moved". */
  changedDocs: string[];
  /** section-trust distribution (F4 — never a single collapsed percentage). */
  trustDistribution: TrustDistribution;
  /** the review watermark this summary is "since" (short SHA for display). */
  sinceSha: string;
}

/** Find the ② knowledge section's members (the 4 canonical docs). */
function knowledgeMembers(detail: BrainDetail): SectionMember[] {
  const sec = detail.sections.find((s) => s.key === 'knowledge');
  return sec?.members ?? [];
}

/**
 * Build the current-DDD weekly-report model from the already-loaded detail + review.
 * Pure; null-safe on an unloaded review or a null trust snapshot.
 */
export function weeklyReportModel(
  detail: BrainDetail,
  review: ReviewData | null,
): WeeklyReportModel {
  const members = knowledgeMembers(detail);
  const signals = docSignalMap(members, review);

  let autoApplied = 0;
  const changedDocs: string[] = [];
  for (const m of members) {
    const sig = signals.get(m.path)!;
    autoApplied += sig.newCount;
    if (sig.newCount > 0 || sig.pendingCount > 0) changedDocs.push(basename(m.path));
  }

  // pending = the brain's escalation queue (proposals). Prefer the detail health
  // count (authoritative), fall back to the review proposals length.
  const pending = detail.health?.escalationPending ?? review?.proposals.length ?? 0;

  // F4: trust DISTRIBUTION, never a rollup. trust is doc→section→level|null.
  const trustDistribution: TrustDistribution = { full: 0, high: 0, moderate: 0, low: 0, unscored: 0 };
  const trust = detail.health?.trust;
  if (trust) {
    for (const sections of Object.values(trust)) {
      for (const level of Object.values(sections)) {
        if (level && (TRUST_LEVELS as readonly string[]).includes(level)) {
          trustDistribution[level as TrustLevel] += 1;
        } else {
          trustDistribution.unscored += 1;
        }
      }
    }
  }

  return {
    autoApplied,
    pending,
    changedDocs,
    trustDistribution,
    sinceSha: (review?.last_reviewed_sha ?? '').slice(0, 8),
  };
}
