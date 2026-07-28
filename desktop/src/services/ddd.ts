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
