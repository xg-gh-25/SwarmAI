/**
 * DDD Brain Hub service — read-only client for the Brain Hub lens.
 *
 * Wraps GET /api/ddd/brains (Gallery) and GET /api/ddd/brains/{name} (Brain view).
 * The backend already emits camelCase, so this is a thin typed client — no
 * snake→camel conversion needed (unlike workspace.ts).
 *
 * Everything here is a live read projection, except confirming a Project Agent from
 * its review card (confirmProjectAgent). No external actions.
 */
import { useQuery } from '@tanstack/react-query';
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
  /** a pipeline run of this project is in progress (its runs, last 30 days) */
  liveRun: boolean;
  /** human "N ago" of this project's newest landed run in the last 30 days, "--" when none */
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
  /** entries in a decaying (dormant/archived) state — a maintenance signal. Emitted
   *  on EVERY health dict (gallery + detail) by _brain_health_base, so it's present
   *  on detail health at runtime; declared here so the Overview §② needs-you can read
   *  it (type must match wire reality — same contract as BrainHealth.sinking). */
  sinking: number;
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
  /** Setup (s_project-manager P0..P6): 'setting-up' from create_project until the P6
   *  gate records 'ready'. null/absent = no record (an older Brain) — never "being born". */
  setupState?: 'setting-up' | 'ready' | null;
  /** While setting up: checks done (PASS + N/A) of all, from the last P6 --record.
   *  null/absent = no P6 run recorded yet, or not setting up. */
  setupProgress?: { done: number; total: number; next?: string | null } | null;
  /** project-agent.yaml: 'draft' waits for the person to confirm; 'invalid' has
   *  problems; null/absent = no file (or an old daemon). */
  projectAgentStatus?: 'draft' | 'reviewed' | 'invalid' | null;
  /** health.pending split by source: escalation | paused_run | cultivation | job. */
  pendingBySource?: Record<string, number>;
  health: BrainHealth;
  /** 7-type histogram for the compact card's 3-layer ontology bar. Rides the
   *  gallery's existing single parse (zero extra glob). OPTIONAL for daemon skew:
   *  an old daemon omits it → the compact bar just doesn't render. */
  typeCounts?: Record<EntryType, number>;
  /** One-line "what this DDD is" briefing for the gallery card (item 2, run_3d371424).
   *  Backend reads it straight from each project's aim.json `description` (ZERO extra
   *  scan). OPTIONAL for daemon-skew: an old daemon / a brain without a description in
   *  aim.json omits it → the card simply renders no briefing line. */
  description?: string;
  /** ddd-changelog entries stamped in the last 30 days (the Hub's LEARNED total).
   *  OPTIONAL for daemon skew: an old daemon omits it and the Hub shows UNKNOWN. */
  recentActivity?: number;
}

export interface SectionMember {
  path: string;      // project-relative
  gitStatus: string; // clean | modified | untracked | added | deleted | renamed | conflicting
  /** ② knowledge members ONLY (the 4 DDD-doc hero cards, run_a607f2b0): human
   *  "N ago" of the file's FILESYSTEM mtime (not git — works for gitignored
   *  projects), computed live. OPTIONAL by design: other sections omit it, and a
   *  pre-deploy daemon omits it everywhere — consumers MUST guard (undefined →
   *  render nothing), same contract as BrainDetail.health. */
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
  /** hasCodeIntel — true iff a code_intel.db exists on disk for this brain (a
   *  live PRESENCE check, NOT gated on kind: all DDDs resolve to kind='knowledge').
   *  The CodeIntel nav entry + View-code-graph button gate on THIS, never on kind.
   *  OPTIONAL by design (daemon skew): an old daemon omits it — consumers MUST
   *  treat `undefined` as false (`detail.hasCodeIntel === true` / `?? false`). */
  hasCodeIntel?: boolean;
  /** hasSpecs — true iff the brain has a survey.json (its repos were surveyed). The
   *  Repo documents disclosure renders only then. OPTIONAL (daemon skew): treat
   *  `undefined` as false. */
  hasSpecs?: boolean;
  /** DETAIL-view health metrics (design 2026-08-04, run-1 backend). OPTIONAL by
   *  design (daemon skew): a pre-deploy daemon omits it — consumers MUST guard
   *  (`detail.health` undefined → render nothing), same as hasCodeIntel. */
  health?: DetailHealth;
  /** ⑤ Project Agent definition (project-agent.yaml), generated at Brain creation
   *  from the DDD and reviewed by the person. null = none yet; undefined = an old
   *  daemon that does not send it. `problems` lists unsupported values. */
  projectAgent?: ProjectAgent | null;
  /** Need You items split by source (same as BrainSummary.pendingBySource). */
  pendingBySource?: Record<string, number>;
  /** Setup state (same as BrainSummary.setupState): 'setting-up' shows Continue setup. */
  setupState?: 'setting-up' | 'ready' | null;
}

/** A setup / validations row: a command its source file declares, run in `cwd`
 *  (repo-relative; empty = the repo root) of `repo`. */
export interface AgentCommand { id?: string; command: string; source: string; repo?: string; cwd?: string }

/** One work mode of a Project Agent (schema 2). A mode only narrows the shared part. */
export interface AgentMode {
  id: string;
  when: string;
  profile: string;
  profileRules: { when: string; profile: string }[];
  placements: string[];
  repos: string[];
  setup: AgentCommand[];
  validations: AgentCommand[];
  skills: string[];
  delivery: string;
  prRemote?: string | null;
  reservedForHuman: string[];
  gates: string[];
}

export interface ProjectAgent {
  path: string;
  schema?: number;
  status?: 'draft' | 'reviewed';
  reviewedAt?: string | null;
  reviewedBy?: string | null;
  summary?: string;
  identity?: { summary: string; inScope: string[]; outOfScope: string[]; source: string };
  intake?: { handles: string[]; repos: string[]; proposedAliases?: { name: string; source: string }[] };
  confidence?: string;
  defaultProfile?: string;
  profileRules?: { when: string; profile: string }[];
  reviewFocus?: { area: string; why: string; source: string }[];
  validations?: AgentCommand[];
  reservedForHuman?: string[];
  gates?: string[];
  placements?: string[];
  landing?: Record<string, string>;
  modes?: AgentMode[];
  defaultMode?: string;
  sources?: string[];
  /** sha256 over the definition, the skills its modes use and the Brain's gates;
   *  a confirm names the revision the person saw. */
  revision?: string | null;
  problems: string[];
  /** not problems: e.g. a desktop-only skill in a mode that may run on a Hive */
  notes?: string[];
  /** the host check that every setup / validations source exists, for this revision */
  sourceCheck?: { ok: boolean; checked_at: string; missing: string[] } | null;
  /** {source as written: absolute path} for the sources that resolve to a file */
  sourcePaths?: Record<string, string>;
}

/** The review card's choices (POST …/project-agent/confirm). */
export interface AgentConfirm {
  revision: string;
  remove: string[];
  allow_hive?: boolean;
  aliases: string[];
}

export interface AgentConfirmResult {
  revision: string;
  reviewedAt: string;
  reviewedBy: string;
  aliasesAdded: string[];
  sourceCheck: { ok: boolean; missing: string[] };
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

/** POST /api/ddd/brains/{name}/project-agent/confirm — confirm a Brain's Project
 *  Agent with the review card's choices; `brain` null = the Runner Agent (the
 *  General Brain). 409 when the definition changed since it was shown. */
export async function confirmProjectAgent(brain: string | null, body: AgentConfirm): Promise<AgentConfirmResult> {
  const url = brain === null ? '/ddd/runner-agent/confirm'
    : `/ddd/brains/${encodeURIComponent(brain)}/project-agent/confirm`;
  const resp = await api.post<AgentConfirmResult>(url, body);
  return resp.data;
}

/** A local repository a Brain may read into its code graph (design
 *  2026-10-29-code-intel-unification §0.3): an agent proposes it, a person confirms. */
export interface LocalRoot {
  path: string;
  state: 'pending' | 'confirmed';
  source?: string | null;        // 'agent' | 'tech-md'
  addedAt?: string | null;
  confirmedAt?: string | null;
  graphBuilt: boolean;
}

/** GET /api/ddd/brains/{name}/local-roots */
export async function getLocalRoots(brain: string): Promise<LocalRoot[]> {
  const resp = await api.get<{ localRoots: LocalRoot[] }>(`/ddd/brains/${encodeURIComponent(brain)}/local-roots`);
  return resp.data.localRoots ?? [];
}

/** POST …/local-roots/confirm — the person's Confirm; the graph is built in a child. */
export async function confirmLocalRoot(brain: string, path: string): Promise<LocalRoot[]> {
  const resp = await api.post<{ localRoots: LocalRoot[] }>(`/ddd/brains/${encodeURIComponent(brain)}/local-roots/confirm`, { path });
  return resp.data.localRoots ?? [];
}

/** POST …/local-roots/remove — stop reading the repository into this Brain. */
export async function removeLocalRoot(brain: string, path: string): Promise<LocalRoot[]> {
  const resp = await api.post<{ localRoots: LocalRoot[] }>(`/ddd/brains/${encodeURIComponent(brain)}/local-roots/remove`, { path });
  return resp.data.localRoots ?? [];
}

/** GET /api/ddd/runner-agent — the Runner Agent (null when it is not there). */
export async function getRunnerAgent(): Promise<ProjectAgent | null> {
  const resp = await api.get<{ projectAgent: ProjectAgent | null }>('/ddd/runner-agent');
  return resp.data.projectAgent ?? null;
}

/** One repo document in a brain's survey (design 2026-10-27 §5.3). Tier 1 = read
 *  first (README, architecture, docs the README links to); 2 = design records and
 *  ADRs; 3 = contributing / security / changelog and much-linked docs; 4 = the rest.
 *  `flags` is set when the document reads like a prompt injection or holds a secret
 *  pattern: show it, it is still repo content to treat as data. */
export interface SpecDoc {
  path: string;
  tier: 1 | 2 | 3 | 4;
  signals: string[];
  flags?: string[] | null;
}

export interface SpecRepo {
  repo: string;
  head: string | null;
  docs: SpecDoc[];
  api_contracts: { path: string; format: string; items: number }[];
}

export interface BrainSpecs {
  available: boolean;
  generated_at?: string;
  repos: SpecRepo[];
}

/** GET /api/ddd/brains/{name}/specs — the brain's repo documents by tier (no text). */
export async function getBrainSpecs(name: string): Promise<BrainSpecs> {
  const resp = await api.get<BrainSpecs>(`/ddd/brains/${encodeURIComponent(name)}/specs`);
  return resp.data;
}

/** GET /api/ddd/brains/{name}/specs/doc — one repo document's text. The backend
 *  re-confines the path to the repo the brain owns; never pass it anything else. */
export async function getBrainSpecDoc(name: string, repo: string, path: string): Promise<string> {
  const resp = await api.get<{ text: string }>(
    `/ddd/brains/${encodeURIComponent(name)}/specs/doc`, { params: { repo, path } });
  return resp.data.text ?? '';
}

/** One recall hit from the Brain Hub search box (mirrors the backend hit shape
 *  assembled in ddd_brain.py::brain_recall). `source` is a workspace-relative
 *  path (Projects/<name>/…) — directly openable via swarm:open-file. */
export interface RecallHit {
  domain: string;
  title: string;
  source: string;
  content: string;
}

/** GET /api/ddd/brains/{name}/recall?q= — the Brain Hub's search box (the endpoint
 *  docstring names it verbatim). Fail-soft by contract: any backend failure returns
 *  empty hits, never a 500. A blank/whitespace query short-circuits to [] WITHOUT a
 *  network call — the debounced input fires per keystroke, and the endpoint already
 *  returns empty for blank q, so the call would be pure waste. */
export async function brainRecall(name: string, q: string): Promise<RecallHit[]> {
  if (!q.trim()) return [];
  const resp = await api.get<{ query: string; count: number; hits?: RecallHit[] }>(
    `/ddd/brains/${encodeURIComponent(name)}/recall`,
    { params: { q } },
  );
  return resp.data.hits ?? [];
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

// ── React Query hooks (run_cfb460ac) ───────────────────────────────────────────
// Brain Hub used raw api.get().then() in useEffect, so switching Overview↔Browse↔
// Review or re-opening the same brain refetched every time (the slow-reopen). These
// useQuery hooks cache per brain name (staleTime 30s) so a re-open inside the window
// is served from cache — 0 network calls. Mirrors LibraryOverlay's useQuery pattern.
// `enabled: !!name` so a null/empty name never fires. All three share the same
// caching contract; the underlying fetchers above stay usable for non-React callers.

const BRAIN_STALE_MS = 30_000;

/** Cached GET /ddd/brains — the Gallery summaries + pinned order. Re-opening the
 *  overlay within the window is instant (the aggregate scan is the ~4s cold cost). */
export function useBrainsWithPinned() {
  return useQuery<{ brains: BrainSummary[]; pinned: string[] }>(brainsWithPinnedQuery());
}

/** The options of the Gallery query — also what the Brain Hub nav item prefetches. */
export function brainsWithPinnedQuery() {
  return { queryKey: ['brains-with-pinned'] as const, queryFn: getBrainsWithPinned, staleTime: BRAIN_STALE_MS };
}

/** Cached GET /ddd/runner-agent — the Runner Agent (the General Brain's Project Agent). */
export function useRunnerAgent(enabled = true) {
  return useQuery<ProjectAgent | null>({
    queryKey: ['runner-agent'],
    queryFn: getRunnerAgent,
    enabled,
    staleTime: BRAIN_STALE_MS,
  });
}

/** Cached GET /ddd/brains/{name} — the six-section detail. */
export function useBrainDetail(name: string | null) {
  return useQuery<BrainDetail>({
    queryKey: ['brain-detail', name],
    queryFn: () => getBrainDetail(name as string),
    enabled: !!name,
    staleTime: BRAIN_STALE_MS,
  });
}

/** Cached GET /ddd/brains/{name}/specs — fetched only when `enabled` (the
 *  disclosure is open). */
export function useBrainSpecs(name: string, enabled: boolean) {
  return useQuery<BrainSpecs>({
    queryKey: ['brain-specs', name],
    queryFn: () => getBrainSpecs(name),
    enabled: enabled && !!name,
    staleTime: BRAIN_STALE_MS,
  });
}

/** Cached GET /ddd/brains/{name}/specs/doc — one repo document's text. */
export function useBrainSpecDoc(name: string, repo: string, path: string) {
  return useQuery<string>({
    queryKey: ['brain-spec-doc', name, repo, path],
    queryFn: () => getBrainSpecDoc(name, repo, path),
    staleTime: BRAIN_STALE_MS,
  });
}

/** Cached GET /ddd/brains/{name}/review — the tagged review queue. */
export function useReview(name: string | null) {
  return useQuery<ReviewData>({
    queryKey: ['brain-review', name],
    queryFn: () => getReview(name as string),
    enabled: !!name,
    staleTime: BRAIN_STALE_MS,
  });
}

/** Cached GET /ddd/brains/{name}/distribution — declared reach + output state. */
export function useDistribution(name: string | null) {
  return useQuery<DistributionState>({
    queryKey: ['brain-distribution', name],
    queryFn: () => getDistribution(name as string),
    enabled: !!name,
    staleTime: BRAIN_STALE_MS,
  });
}
