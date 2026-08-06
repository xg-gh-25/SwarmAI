/**
 * CapabilitiesOverlay — fail-safe grouping logic (run_b5d98151 §5).
 *
 * These exercise the pure groupSkills/orderedCategories helpers that back the
 * Skills view. The load-bearing invariants (Gate-1 adopted):
 *   • an EMPTY category group (esp. Internal) is NEVER emitted (no void, no crash);
 *   • a skill MISSING category/visibility falls to Utilities/public, never vanishes;
 *   • signature skills are pulled into heroes (not double-rendered as rows);
 *   • Internal is ordered LAST.
 */
import { describe, it, expect } from 'vitest';
import { groupSkills, orderedCategories } from './CapabilitiesOverlay';
import type { Skill } from '../../types';

function skill(partial: Partial<Skill> & { folderName: string }): Skill {
  return {
    name: partial.folderName.replace(/^s_/, ''),
    description: 'desc',
    version: '1.0.0',
    sourceTier: 'built-in',
    readOnly: true,
    category: 'Utilities',
    visibility: 'public',
    ...partial,
  };
}

describe('orderedCategories', () => {
  it('puts Internal last and known categories in canonical order', () => {
    const out = orderedCategories(['Internal', 'Writing', 'Research', 'ZzzUnknown']);
    expect(out[out.length - 1]).toBe('Internal');
    expect(out.indexOf('Research')).toBeLessThan(out.indexOf('Writing'));
    // unknown category sorts after known ones but before Internal
    expect(out.indexOf('ZzzUnknown')).toBeLessThan(out.indexOf('Internal'));
  });
});

describe('groupSkills — fail-safe (§5)', () => {
  it('never emits an empty group when NO internal skills are present', () => {
    const skills = [
      skill({ folderName: 's_deep-research', category: 'Research' }),
      skill({ folderName: 's_narrative-writing', category: 'Writing' }),
    ];
    const { groups } = groupSkills(skills);
    const cats = groups.map(([c]) => c);
    expect(cats).not.toContain('Internal'); // no internal → no Internal group, no void
    expect(groups.every(([, list]) => list.length > 0)).toBe(true);
  });

  it('renders an Internal group only when >=1 internal skill exists', () => {
    const skills = [
      skill({ folderName: 's_deep-research', category: 'Research' }),
      skill({ folderName: 's_cmhk-weekly-report', category: 'Internal', visibility: 'internal' }),
    ];
    const { groups } = groupSkills(skills);
    expect(groups.map(([c]) => c)).toContain('Internal');
  });

  it('a skill missing category falls to Utilities, never vanishes', () => {
    // simulate a defensive undefined category (service defaults to Utilities, but guard anyway)
    const s = skill({ folderName: 's_mystery' });
    // @ts-expect-error — force the missing-field path
    s.category = undefined;
    const { groups } = groupSkills([s]);
    const all = groups.flatMap(([, list]) => list);
    expect(all.map((x) => x.folderName)).toContain('s_mystery');
    expect(groups.map(([c]) => c)).toContain('Utilities');
  });

  it('pulls signature skills into heroes, not rows', () => {
    const skills = [
      skill({ folderName: 's_autonomous-pipeline', category: 'Automation' }),
      skill({ folderName: 's_pollinate', category: 'Content' }),
      skill({ folderName: 's_deep-research', category: 'Research' }),
    ];
    const { heroes, groups } = groupSkills(skills);
    expect(heroes.map((h) => h.folderName).sort()).toEqual(['s_autonomous-pipeline', 's_pollinate']);
    // heroes must NOT also appear as rows
    const rowFolders = groups.flatMap(([, list]) => list.map((s) => s.folderName));
    expect(rowFolders).not.toContain('s_autonomous-pipeline');
    expect(rowFolders).toContain('s_deep-research');
  });

  it('empty input produces no groups and no heroes (no throw)', () => {
    const { heroes, groups } = groupSkills([]);
    expect(heroes).toEqual([]);
    expect(groups).toEqual([]);
  });
});
