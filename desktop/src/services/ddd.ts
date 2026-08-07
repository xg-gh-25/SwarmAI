/**
 * DDD Brain Hub service — read-only client for the Brain Hub lens.
 *
 * Wraps GET /api/ddd/brains (Gallery) and GET /api/ddd/brains/{name} (Brain view).
 * The backend already emits camelCase, so this is a thin typed client — no
 * snake→camel conversion needed (unlike workspace.ts).
 *
 * Everything here is a live read projection: no writes, no external actions.
 */
import api from './api';

// ── Types (mirror routers/ddd_brain.py response shapes) ──────────────────────

export type SectionKey =
  | 'identity' | 'knowledge' | 'gates'
  | 'capabilities' | 'delivery' | 'refresher';

export interface BrainHealth {
  /** count of entries with decay_state ∈ {dormant, archived} — live */
  sinking: number;
  /** count of staged risky proposals awaiting a human decision */
  pending: number;
  /** git dirty in this project subtree */
  uncommitted: boolean;
  /** human "N ago" of the last commit touching this project (computed live) */
  lastChangeRelative: string;
}

/** Per-section 5-dimensional score (the stored section_health.json shape).
 *  All scores are 0-100 ints; `trust` is a derived level string. */
export interface SectionDiagnostic {
  staleness?: number;
  completeness?: number;
  usage?: number;
  decay?: number;
  contradiction?: number;
  composite?: number;
  trust?: string;   // full | high | moderate | low
}

/** DETAIL-view health metrics (design 2026-08-04). Distinct from the gallery
 *  BrainHealth above — this is the richer per-open block the run-1 backend added
 *  to GET /ddd/brains/{name} (_brain_detail). Each field is an ADMISSION-passing
 *  metric (owner action + live/read, never a frozen verdict). trust/diagnostics
 *  are read from the scheduled section_health.json → null when not yet computed.
 *  NOTE: there is deliberately NO project-level trust rollup (backend Gate-1 MAJOR
 *  refused to invent one) — the UI reports the per-section DISTRIBUTION, never a
 *  single collapsed verdict. */
export interface DetailHealth {
  /** reclaimable-noise: entries the decay engine would strip. reclaimable>0 → an
   *  owner action (run reclaim). Computed live. */
  noise: { reclaimable: number; rate: number };
  /** doc → section → trust-level string, as stored (no rollup). null = no
   *  scheduled score computed yet. */
  trust: Record<string, Record<string, string | null>> | null;
  /** count of proposals awaiting a human decision (approve/reject via the
   *  existing cultivation endpoint). Always present. */
  escalationPending: number;
  /** recall benchmark — SHOWN BUT EXPERIMENTAL (no cheap per-DDD metric yet):
   *  value is null today; typed number|null so a future score fits without a
   *  breaking change. `experimental` gates the UI chip. */
  recall: { value: number | null; experimental: boolean };
  /** Q3 "is it growing?" — count of ddd-changelog entries STAMPED within 30d.
   *  A MAINTENANCE signal (value≠size: an actively-cultivated brain, NOT a big
   *  one). Undated/old rows excluded (honest under-count). Present (0 when no
   *  changelog); OPTIONAL in the type only for pre-deploy daemon skew. */
  recentActivity?: number;
  /** the 5-dim per-section scores verbatim (doc → {sections: {sec: SectionDiagnostic}}).
   *  null when no scheduled score. Display-only diagnostic detail. */
  diagnostics: Record<string, { sections?: Record<string, SectionDiagnostic> }> | null;
  /** ISO timestamp of the scheduled score, or null if none. */
  computedAt: string | null;
}

export interface BrainSummary {
  name: string;
  kind: string;
  sectionsPresent: Record<SectionKey, boolean>;
  lifecycleStage: 'CREATE' | 'GROW' | 'REVIEW' | 'DISTRIBUTE';
  health: BrainHealth;
  /** 7-type histogram for the compact card's 3-layer ontology bar. Rides the
   *  gallery's existing single parse (zero extra glob). OPTIONAL for daemon skew:
   *  an old daemon omits it → the compact bar just doesn't render. */
  typeCounts?: Record<EntryType, number>;
}

export interface SectionMember {
  path: string;      // project-relative
  gitStatus: string; // clean | modified | untracked | added | deleted | renamed | conflicting
  /** ② knowledge members ONLY (the 4 DDD-doc hero cards, run_a607f2b0): human
   *  "N ago" of the file's FILESYSTEM mtime (not git — works for gitignored
   *  projects), computed live. OPTIONAL by design: other sections omit it, and a
   *  pre-deploy daemon omits it everywhere — consumers MUST guard (undefined →
   *  render nothing), same contract as BrainDetail.specs/health. */
  mtime?: string;
  /** ② knowledge members ONLY: this doc's own entry count (per-file, not the
   *  project total). OPTIONAL — same daemon-skew guard as mtime. */
  entryCount?: number;
}

export type EntryType =
  | 'guideline' | 'pitfall' | 'decision' | 'model'
  | 'process' | 'principle' | 'correction';

export type DecayState = 'active' | 'dormant' | 'archived';

export interface KnowledgeEntry {
  title: string;
  entryType: EntryType;
  decayState: DecayState;
  section: string;
  source: string;
  file: string;
}

export interface BrainSection {
  key: SectionKey;
  num: string;          // circled number ①..⑥
  label: string;
  ownGovern: 'OWN' | 'GOVERN';
  curator: string;
  members: SectionMember[];
  entries: KnowledgeEntry[];   // populated for ② knowledge only
  /** R31: an empty section (esp. ③Gates) is COMPLETE, not degraded */
  completeNotBroken: boolean;
}

export interface BrainDetail {
  name: string;
  kind: string;
  sections: BrainSection[];
  /** spec-details/*.spec.md filenames — a DERIVED PROJECTION (NOT a section);
   *  [] when the brain has no spec-details/ dir. Backend emits camelCase-safe
   *  single-word key; getBrainDetail is a direct passthrough (no transform).
   *  OPTIONAL by design: an old daemon (pre-deploy skew) omits it — consumers
   *  MUST keep the `?? []` guard (meta-review: type must match wire reality). */
  specs?: string[];
  /** hasCodeIntel — true iff a code_intel.db exists on disk for this brain (a
   *  live PRESENCE check, NOT gated on kind: all DDDs resolve to kind='knowledge').
   *  The CodeIntel nav entry + View-code-graph button gate on THIS, never on kind.
   *  OPTIONAL by design (daemon skew): an old daemon omits it — consumers MUST
   *  treat `undefined` as false (`detail.hasCodeIntel === true` / `?? false`). */
  hasCodeIntel?: boolean;
  /** DETAIL-view health metrics (design 2026-08-04, run-1 backend). OPTIONAL by
   *  design (daemon skew): a pre-deploy daemon omits it — consumers MUST guard
   *  (`detail.health` undefined → render nothing), same as specs/hasCodeIntel. */
  health?: DetailHealth;
}

// ── Review tab (Run 2) ────────────────────────────────────────────────────────

/** Provenance tag for a review hunk. (decay·sinking removed — the backend
 *  _tag_hunk never emits it; the Gallery's health.sinking count carries that signal.) */
export type HunkTag = 'cultivation·auto-applied' | 'risky·staged';

export interface ReviewHunk {
  file: string;         // project-relative path the hunk touches
  /** content signature — stable id used by reject (NOT a position index) */
  signature: string;
  tag: HunkTag;
  diff_text: string;    // self-contained single-hunk patch (@@ … block)
}

export interface PendingProposal {
  id: string;
  target_doc: string;
  target_section: string;
  content: string;
  confidence: number | null;
  source_run_id: string;
}

export interface ReviewData {
  last_reviewed_sha: string;
  head_sha: string;
  hunks: ReviewHunk[];
  proposals: PendingProposal[];
  /** true if the scoped git-diff timed out — the hunk list is INCOMPLETE, so
   *  "Mark all seen" must be disabled (advancing the watermark over an
   *  empty-because-timed-out queue would silently mark unreviewed work as seen). */
  diff_incomplete: boolean;
}

// ── Distribute tab (Run 3) ────────────────────────────────────────────────────

/** A DDD's live distribution state — declared reach + output state. No stored metric. */
export interface DistributionState {
  /** declared targets (aim-capabilities / open-plugin); [] = not declared */
  declared_targets: string[];
  visibility: string;                    // internal | external
  distributable: boolean;                // true iff declared_targets non-empty
  declared: boolean;                     // aim.json had a distribution block
  warnings: string[];                    // policy warnings (malformed/unknown token)
  has_output: boolean;                   // a distribute output exists under .artifacts/
  output_path: string | null;            // the .artifacts/<name> stem
  last_distribute_time: string | null;   // ISO, from output dir mtime (display-only)
  /** TRISTATE: true = knowledge changed since last distribute; false = up to date;
   *  null = freshness UNKNOWN (the output dir isn't git-committed, so there's no
   *  reliable commit anchor to compare against — never assert a confident boolean). */
  source_changed_since: boolean | null;
}

// ── API ──────────────────────────────────────────────────────────────────────

/** GET /api/ddd/brains — Gallery: one live summary per DDD project. */
export async function getBrains(): Promise<BrainSummary[]> {
  const resp = await api.get<{ brains: BrainSummary[] }>('/ddd/brains');
  return resp.data.brains ?? [];
}

/** Gallery + Welcome Top-N need BOTH the summaries AND the pinned order (SwarmAI
 *  first + focus projects, existence-guarded, from the backend registry). One
 *  round-trip: `pinned` is a sibling field on /ddd/brains. */
export async function getBrainsWithPinned(): Promise<{ brains: BrainSummary[]; pinned: string[] }> {
  const resp = await api.get<{ brains: BrainSummary[]; pinned?: string[] }>('/ddd/brains');
  return { brains: resp.data.brains ?? [], pinned: resp.data.pinned ?? [] };
}

/** GET /api/ddd/brains/{name} — Brain view: six-section breakdown. */
export async function getBrainDetail(name: string): Promise<BrainDetail> {
  const resp = await api.get<BrainDetail>(`/ddd/brains/${encodeURIComponent(name)}`);
  return resp.data;
}

/** Aggregate a brain's ② knowledge entries into a per-type count for the DddCard
 *  type-mix bar. The CONSUMER computes this (DddCard never touches sections) — it's
 *  the data-plumbing fix for the 7-type×3-layer bar (Gate-1: entries live on
 *  detail.sections[].entries, not on DetailHealth). Returns undefined when there
 *  are no entries, so the card omits the bar entirely (no vanity empty bar). */
export function aggregateTypeCounts(sections: BrainSection[]): Record<EntryType, number> | undefined {
  const counts: Record<EntryType, number> = {
    guideline: 0, pitfall: 0, decision: 0, model: 0, process: 0, principle: 0, correction: 0,
  };
  let total = 0;
  for (const s of sections) {
    for (const e of s.entries) {
      counts[e.entryType] = (counts[e.entryType] ?? 0) + 1;
      total += 1;
    }
  }
  return total > 0 ? counts : undefined;
}

// ── Review API (Run 2) ────────────────────────────────────────────────────────

/** GET /api/ddd/brains/{name}/review — tagged git-diff hunks since watermark. */
export async function getReview(name: string): Promise<ReviewData> {
  const resp = await api.get<ReviewData>(`/ddd/brains/${encodeURIComponent(name)}/review`);
  return resp.data;
}

/** POST …/review/approve — advance the last-reviewed watermark to HEAD. */
export async function approveReview(name: string): Promise<{ last_reviewed_sha: string }> {
  const resp = await api.post(`/ddd/brains/${encodeURIComponent(name)}/review/approve`);
  return resp.data;
}

/** POST …/review/reject — reverse-apply ONE hunk (by content signature). */
export async function rejectReviewHunk(
  name: string, file: string, hunkSignature: string,
): Promise<{ reverted: boolean }> {
  const resp = await api.post(
    `/ddd/brains/${encodeURIComponent(name)}/review/reject`,
    { file, hunk_signature: hunkSignature },
  );
  return resp.data;
}

// ── Zone C: risky proposals delegate to the EXISTING cultivation router ────────
// (no reinvented apply logic — cultivation.py already stages + applies/reverts)

export async function approveProposal(id: string, project: string): Promise<unknown> {
  const resp = await api.post(
    `/cultivation/proposals/${encodeURIComponent(id)}/approve?project=${encodeURIComponent(project)}`,
  );
  return resp.data;
}

export async function rejectProposal(id: string, project: string): Promise<unknown> {
  const resp = await api.post(
    `/cultivation/proposals/${encodeURIComponent(id)}/reject?project=${encodeURIComponent(project)}`,
  );
  return resp.data;
}

/** GET …/distribution — live declared reach + output state (read-only). */
export async function getDistribution(name: string): Promise<DistributionState> {
  const resp = await api.get<DistributionState>(
    `/ddd/brains/${encodeURIComponent(name)}/distribution`,
  );
  return resp.data;
}
