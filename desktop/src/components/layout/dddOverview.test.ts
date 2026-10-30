/**
 * dddOverview — pure-helper tests (run_6c68088f).
 *
 * The two data helpers behind the Brain-detail Overview tab:
 *   - docSignalMap(members, knowledge) → per-canonical-doc {newCount}
 *   - weeklyReportModel(detail, knowledge) → current-DDD-only weekly summary
 *
 * Correctness contracts pinned here:
 *   F2a: match the week's entries by BASENAME (`item.doc`) — member.path may be
 *        '2-understanding/TECH.md' (migrated) OR bare 'PRODUCT.md' (fallback).
 *   F4:  weeklyReportModel emits the trust DISTRIBUTION, never a collapsed
 *        trustPct rollup (backend Gate-1 refused a project-composite).
 */
import { describe, it, expect } from 'vitest';
import type { BrainDetail, BrainKnowledge, SectionMember } from '../../services/ddd';
import { docSignalMap, weeklyReportModel } from './dddOverview';

const MEMBERS: SectionMember[] = [
  { path: '2-understanding/PRODUCT.md', gitStatus: 'clean', mtime: '5d ago', entryCount: 10 },
  { path: '2-understanding/TECH.md', gitStatus: 'modified', mtime: '2h ago', entryCount: 40 },
  { path: '2-understanding/IMPROVEMENT.md', gitStatus: 'clean', mtime: '1d ago', entryCount: 25 },
  { path: '2-understanding/PROJECT.md', gitStatus: 'clean', mtime: '3h ago', entryCount: 8 },
];

const item = (doc: string, title: string, type = 'decision') => ({
  anchor: `Projects/SwarmAI/2-understanding/${doc}#S#cand:${title}`, unit_id: title, doc,
  path: `Projects/SwarmAI/2-understanding/${doc}`, section: 'S', type, gap_kind: type === 'gap' ? 'bug' : null,
  title, text: title, written_at: '2026-10-30T10:00:00Z', collected: false, source: 'S7', source_ref: null, adds_to: null,
});
// this week: a TECH.md decision and a TECH.md gap (an open item is a written entry too)
const KNOWLEDGE: BrainKnowledge = {
  brain: 'SwarmAI', since: 'week',
  layers: [{ layer: 'meta', items: [] }, { layer: 'cognitive', items: [item('TECH.md', 'a')] }, { layer: 'operational', items: [] }],
  gaps: [item('TECH.md', 'g', 'gap')],
  counts: { week: 2, all: 9 }, withdrawn: { count: 0, items: [] },
};

describe('docSignalMap (AC5, F2a)', () => {
  it('counts the week\'s entries per canonical doc by basename, gaps included', () => {
    const m = docSignalMap(MEMBERS, KNOWLEDGE);
    expect(m.get('2-understanding/TECH.md')?.newCount).toBe(2);
  });

  it('docs with no signal are present with zero counts (fixed layout, always 4)', () => {
    const m = docSignalMap(MEMBERS, KNOWLEDGE);
    expect(m.get('2-understanding/PRODUCT.md')).toEqual({ newCount: 0 });
    expect(m.get('2-understanding/IMPROVEMENT.md')).toEqual({ newCount: 0 });
    expect(m.get('2-understanding/PROJECT.md')).toEqual({ newCount: 0 });
  });

  it('null knowledge (not loaded) → all-zero, never throws', () => {
    const m = docSignalMap(MEMBERS, null);
    expect(m.get('2-understanding/TECH.md')).toEqual({ newCount: 0 });
  });

  it('bare-path member (un-migrated fallback) still matches its entries', () => {
    const bare: SectionMember[] = [{ path: 'TECH.md', gitStatus: 'modified' }];
    const m = docSignalMap(bare, KNOWLEDGE);
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

  it('summarizes what the DDD learned this week + pending counts', () => {
    const r = weeklyReportModel(DETAIL, KNOWLEDGE);
    expect(r.learnedWeek).toBe(2);
    expect(r.pending).toBe(1);
  });

  it('lists which of the 4 core docs changed (by name)', () => {
    const r = weeklyReportModel(DETAIL, KNOWLEDGE);
    expect(r.changedDocs).toContain('TECH.md');
    expect(r.changedDocs).not.toContain('PRODUCT.md');
    expect(r.changedDocs).not.toContain('IMPROVEMENT.md');
  });

  it('F4: emits a trust DISTRIBUTION, never a collapsed trustPct rollup', () => {
    const r = weeklyReportModel(DETAIL, KNOWLEDGE);
    // distribution counts the section-level trust levels across docs
    expect(r.trustDistribution).toEqual({ full: 1, high: 1, moderate: 1, low: 1, unscored: 0 });
    // the refused rollup must NOT exist on the model
    expect((r as Record<string, unknown>).trustPct).toBeUndefined();
  });

  it('null trust (unscored) → distribution is all-unscored-safe, no throw', () => {
    const d = { ...DETAIL, health: { ...DETAIL.health!, trust: null } } as unknown as BrainDetail;
    const r = weeklyReportModel(d, KNOWLEDGE);
    expect(r.trustDistribution).toEqual({ full: 0, high: 0, moderate: 0, low: 0, unscored: 0 });
  });
});
