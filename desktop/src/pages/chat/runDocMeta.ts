/**
 * What each pipeline run document is, for the chat: an icon and the words saying what
 * it holds. The backend sends only the file name (backend/core/pipeline_stage_events.py); the words
 * are the frontend's so they follow the UI language. Shared by the stage document
 * cards (StageDocs) and the run's end summary (TurnCards).
 */

import { tf } from './tf';

export const DOC_META: Record<string, { icon: string; key: string; purpose: string }> = {
  'REQUIREMENTS.md': { icon: 'checklist', key: 'chat.turn.summary.doc.requirements', purpose: 'Requirements · root cause · clarifications' },
  'DESIGN_SPEC.md': { icon: 'architecture', key: 'chat.turn.summary.doc.design', purpose: 'Approach · architecture & flow · invariants' },
  'REVIEW.md': { icon: 'verified_user', key: 'chat.turn.summary.doc.review', purpose: 'Gates · expert reviews · how each finding was settled' },
  'FIXING_SPEC.md': { icon: 'build', key: 'chat.turn.summary.doc.fixing', purpose: 'Fixes · each finding with its break test' },
  'REPORT.md': { icon: 'summarize', key: 'chat.turn.summary.doc.report', purpose: 'Full run report' },
};

/** The icon and the translated purpose of a run document (a name we do not know: a
 *  generic icon and the backend's own purpose text). */
export function docMeta(t: Parameters<typeof tf>[0], name: string, fallback?: string | null): { icon: string; purpose: string } {
  const meta = DOC_META[name];
  if (!meta) return { icon: 'description', purpose: fallback ?? '' };
  return { icon: meta.icon, purpose: tf(t, meta.key, meta.purpose) };
}
