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

export interface BrainSummary {
  name: string;
  kind: string;
  sectionsPresent: Record<SectionKey, boolean>;
  lifecycleStage: 'CREATE' | 'GROW' | 'REVIEW' | 'DISTRIBUTE';
  health: BrainHealth;
}

export interface SectionMember {
  path: string;      // project-relative
  gitStatus: string; // clean | modified | untracked | added | deleted | renamed | conflicting
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

/** GET /api/ddd/brains/{name} — Brain view: six-section breakdown. */
export async function getBrainDetail(name: string): Promise<BrainDetail> {
  const resp = await api.get<BrainDetail>(`/ddd/brains/${encodeURIComponent(name)}`);
  return resp.data;
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
