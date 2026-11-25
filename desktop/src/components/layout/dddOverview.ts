/**
 * dddOverview — pure data helpers for the Brain-detail Overview tab (run_6c68088f).
 *
 * The Overview tab (in BrainHub.tsx) answers "what state is this brain in / what
 * should I do". These two PURE functions derive its two data-backed signals from
 * data ALREADY loaded (getBrainDetail + getBrainKnowledge) — no new backend, no React.
 * Extracted so the mapping logic is mutation-testable (GUI33 extract-intent-to-
 * pure-helper); the component is a thin renderer over these.
 *
 *   docSignalMap(members, knowledge)    → per-canonical-doc {newCount}
 *   weeklyReportModel(detail, knowledge) → current-DDD-only weekly summary model
 *
 * ── Correctness contracts (do NOT regress — each has a pinned test) ──
 * F2a  An item matches a doc by BASENAME (`item.doc` is the file name). The knowledge
 *      items come only from the four canonical docs (core/brain_knowledge.brain_files),
 *      never from the 2-understanding/knowledge/ corpus, so a corpus file that shares a
 *      canonical basename cannot be counted. `member.path` may be the migrated
 *      '2-understanding/TECH.md' OR the un-migrated bare 'TECH.md' — basename keying
 *      handles both.
 * F4   No section-trust figure: the score had no producer any more and was removed.
 */
import type { BrainDetail, BrainKnowledge, SectionMember } from '../../services/ddd';

/** Per-doc signal for a core-doc card: entries CDF wrote into it in the last 7 days. */
export interface DocSignal {
  newCount: number;
}

/** basename of a project-relative member path ('2-understanding/TECH.md' → 'TECH.md';
 *  bare 'TECH.md' → 'TECH.md'). */
function basename(p: string): string {
  return p.split('/').pop() ?? p;
}

/**
 * Map each canonical ② doc member to its {newCount}: the entries of the week-range
 * knowledge response written into it (gaps included — they are written entries too).
 *
 * Returns a Map keyed by the FULL member.path (the stable card identity). EVERY passed
 * member gets an entry (zero when nothing new) — the Overview shows a fixed set of doc
 * cards regardless of activity (XG: fixed layout, no dynamic reorder).
 *
 * Pure; null-safe on a not-yet-loaded response. Pass the `week` response: the count is
 * "new this week", the same reference the Hub card uses.
 */
export function docSignalMap(
  members: SectionMember[],
  knowledge: BrainKnowledge | null,
): Map<string, DocSignal> {
  const out = new Map<string, DocSignal>();
  for (const m of members) out.set(m.path, { newCount: 0 });
  if (!knowledge) return out;
  const items = [...knowledge.layers.flatMap((l) => l.items), ...knowledge.gaps];
  for (const it of items) {
    const m = members.find((mm) => basename(mm.path) === it.doc);
    if (m) out.get(m.path)!.newCount += 1;
  }
  return out;
}

/** The current-DDD-only weekly-report model (AC7). Everything is derived from data
 *  already loaded — no global one-pot, no file written. */
export interface WeeklyReportModel {
  /** entries CDF wrote across ALL canonical docs in the last 7 days. */
  learnedWeek: number;
  /** this brain's Need-You items (the escalation queue). */
  pending: number;
  /** basenames of the core docs with any signal (new) — "what moved". */
  changedDocs: string[];
}

/** Find the ② knowledge section's members (the 4 canonical docs). */
function knowledgeMembers(detail: BrainDetail): SectionMember[] {
  const sec = detail.sections.find((s) => s.key === 'knowledge');
  return sec?.members ?? [];
}

/**
 * Build the current-DDD weekly-report model from the already-loaded detail + the
 * week-range knowledge. Pure; null-safe on an unloaded response.
 */
export function weeklyReportModel(
  detail: BrainDetail,
  knowledge: BrainKnowledge | null,
): WeeklyReportModel {
  const members = knowledgeMembers(detail);
  const signals = docSignalMap(members, knowledge);

  let learnedWeek = 0;
  const changedDocs: string[] = [];
  for (const m of members) {
    const sig = signals.get(m.path)!;
    learnedWeek += sig.newCount;
    if (sig.newCount > 0) changedDocs.push(basename(m.path));
  }

  // pending = the brain's escalation queue (the detail health count).
  const pending = detail.health?.escalationPending ?? 0;

  return {
    learnedWeek,
    pending,
    changedDocs,
  };
}
