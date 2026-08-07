/**
 * dddOverview — pure-helper tests (run_6c68088f).
 *
 * The two data helpers behind the Brain-detail Overview tab:
 *   - docSignalMap(members, review) → per-canonical-doc {newCount, pendingCount}
 *   - weeklyReportModel(detail, review) → current-DDD-only weekly summary
 *
 * Gate-1 correctness fixes pinned here (BLOCK→PASS):
 *   F2a: match hunks by BASENAME + exclude the 2-understanding/knowledge/ recall
 *        corpus (mirrors backend _tag_hunk ddd_brain.py:985) — member.path may be
 *        '2-understanding/TECH.md' (migrated) OR bare 'PRODUCT.md' (fallback).
 *   F2b: proposal.target_doc is a BARE filename ('TECH.md') → match by
 *        basename(member.path), never full-path equality.
 *   F4:  weeklyReportModel emits the trust DISTRIBUTION, never a collapsed
 *        trustPct rollup (backend Gate-1 refused a project-composite).
 */
import { describe, it, expect } from 'vitest';
import type { BrainDetail, ReviewData, SectionMember } from '../../services/ddd';
import { docSignalMap, weeklyReportModel } from './dddOverview';

const MEMBERS: SectionMember[] = [
  { path: '2-understanding/PRODUCT.md', gitStatus: 'clean', mtime: '5d ago', entryCount: 10 },
  { path: '2-understanding/TECH.md', gitStatus: 'modified', mtime: '2h ago', entryCount: 40 },
  { path: '2-understanding/IMPROVEMENT.md', gitStatus: 'clean', mtime: '1d ago', entryCount: 25 },
  { path: '2-understanding/PROJECT.md', gitStatus: 'clean', mtime: '3h ago', entryCount: 8 },
];

const REVIEW: ReviewData = {
  last_reviewed_sha: 'a00ae460',
  head_sha: 'ddbcfcd8',
  hunks: [
    // a TECH.md auto-applied hunk (workspace-relative, +++ b/ stripped)
    { file: 'Projects/SwarmAI/2-understanding/TECH.md', signature: 'sigA1', tag: 'cultivation·auto-applied', diff_text: '@@ -1 +1 @@\n-a\n+b' },
    // a SECOND TECH.md hunk → newCount should aggregate to 2
    { file: 'Projects/SwarmAI/2-understanding/TECH.md', signature: 'sigA2', tag: 'cultivation·auto-applied', diff_text: '@@ -5 +5 @@\n-c\n+d' },
    // ⚠️ F2a collision trap: a recall-CORPUS PRODUCT.md — must NOT count as the canonical PRODUCT.md doc
    { file: 'Projects/SwarmAI/2-understanding/knowledge/designs/PRODUCT.md', signature: 'sigCorpus', tag: 'cultivation·auto-applied', diff_text: '@@ -1 +1 @@\n-x\n+y' },
  ],
  proposals: [
    // F2b: target_doc is a BARE filename
    { id: 'p1', target_doc: 'PRODUCT.md', target_section: 'Strategic', content: '...', confidence: 0.7, source_run_id: 'run_x' },
  ],
  diff_incomplete: false,
};

describe('docSignalMap (AC5, F2a/F2b)', () => {
  it('aggregates auto-applied hunks per canonical doc by basename', () => {
    const m = docSignalMap(MEMBERS, REVIEW);
    expect(m.get('2-understanding/TECH.md')?.newCount).toBe(2);
  });

  it('F2a: a recall-corpus /knowledge/ PRODUCT.md does NOT count as the canonical PRODUCT.md', () => {
    const m = docSignalMap(MEMBERS, REVIEW);
    // The only PRODUCT.md signal is the proposal (pending), NOT the corpus hunk.
    expect(m.get('2-understanding/PRODUCT.md')?.newCount).toBe(0);
  });

  it('F2b: proposal.target_doc (bare filename) maps to the canonical doc by basename', () => {
    const m = docSignalMap(MEMBERS, REVIEW);
    expect(m.get('2-understanding/PRODUCT.md')?.pendingCount).toBe(1);
    expect(m.get('2-understanding/TECH.md')?.pendingCount).toBe(0);
  });

  it('docs with no signal are present with zero counts (fixed layout, always 4)', () => {
    const m = docSignalMap(MEMBERS, REVIEW);
    expect(m.get('2-understanding/IMPROVEMENT.md')).toEqual({ newCount: 0, pendingCount: 0 });
    expect(m.get('2-understanding/PROJECT.md')).toEqual({ newCount: 0, pendingCount: 0 });
  });

  it('null review (not loaded) → all-zero, never throws', () => {
    const m = docSignalMap(MEMBERS, null);
    expect(m.get('2-understanding/TECH.md')).toEqual({ newCount: 0, pendingCount: 0 });
  });

  it('bare-path member (un-migrated fallback) still matches its hunk', () => {
    const bare: SectionMember[] = [{ path: 'TECH.md', gitStatus: 'modified' }];
    const m = docSignalMap(bare, REVIEW);
    expect(m.get('TECH.md')?.newCount).toBe(2);
  });
});

describe('weeklyReportModel (AC7, F4)', () => {
  const DETAIL = {
    name: 'SwarmAI', kind: 'code-repo',
    sections: [
      { key: 'knowledge', num: '②', label: 'Knowledge', ownGovern: 'OWN', curator: 'PM',
        members: MEMBERS, entries: [], completeNotBroken: false },
    ],
    health: {
      noise: { reclaimable: 3, rate: 0.12 },
      trust: {
        'TECH.md': { Architecture: 'full', Runtime: 'moderate' },
        'PRODUCT.md': { Vision: 'high', Risks: 'low' },
      },
      escalationPending: 1,
      recall: { value: null, experimental: true },
      diagnostics: null,
      computedAt: '2026-08-01T18:27:57Z',
    },
  } as unknown as BrainDetail;

  it('summarizes current-DDD auto-applied + pending counts', () => {
    const r = weeklyReportModel(DETAIL, REVIEW);
    expect(r.autoApplied).toBe(2);   // the 2 canonical TECH.md hunks (corpus excluded)
    expect(r.pending).toBe(1);
  });

  it('lists which of the 4 core docs changed (by name)', () => {
    const r = weeklyReportModel(DETAIL, REVIEW);
    expect(r.changedDocs).toContain('TECH.md');
    expect(r.changedDocs).toContain('PRODUCT.md');   // has a pending proposal
    expect(r.changedDocs).not.toContain('IMPROVEMENT.md');
  });

  it('F4: emits a trust DISTRIBUTION, never a collapsed trustPct rollup', () => {
    const r = weeklyReportModel(DETAIL, REVIEW);
    // distribution counts the section-level trust levels across docs
    expect(r.trustDistribution).toEqual({ full: 1, high: 1, moderate: 1, low: 1, unscored: 0 });
    // the refused rollup must NOT exist on the model
    expect((r as Record<string, unknown>).trustPct).toBeUndefined();
  });

  it('null trust (unscored) → distribution is all-unscored-safe, no throw', () => {
    const d = { ...DETAIL, health: { ...DETAIL.health!, trust: null } } as unknown as BrainDetail;
    const r = weeklyReportModel(d, REVIEW);
    expect(r.trustDistribution).toEqual({ full: 0, high: 0, moderate: 0, low: 0, unscored: 0 });
  });
});
