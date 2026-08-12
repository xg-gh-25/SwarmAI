/**
 * Regression test for the TSCC OWNER map (TSCCModules ownerOf).
 *
 * The map classifies each context file by its priority-slot OWNER (sys / user /
 * agent / gen), driving the color legend in the Files tab. SELF.md is
 * system(runtime)-owned per SWARMAI.md (priority-2, same slot as SOUL.md), so it
 * must be labeled `sys` with the system blue (#6ea8fe) — not `agent`/purple
 * (run_3f9841d0). This test pins the correct classification against drift.
 *
 * Testing methodology: import the pure `ownerOf` classifier and assert the label
 * + color for the system-owned files and one representative of each other owner.
 */
import { describe, it, expect } from 'vitest';
import { ownerOf } from '../TSCCModules';

describe('TSCC OWNER map — ownerOf', () => {
  it('classifies SELF.md as a system-owned file (sys / blue), matching SWARMAI.md', () => {
    expect(ownerOf('SELF.md')).toEqual({ label: 'sys', color: '#6ea8fe' });
  });

  it('keeps SELF.md consistent with its priority-2 sibling SOUL.md', () => {
    expect(ownerOf('SELF.md')).toEqual(ownerOf('SOUL.md'));
  });

  it('leaves the other owners unchanged', () => {
    expect(ownerOf('SWARMAI.md').label).toBe('sys');
    expect(ownerOf('USER.md').label).toBe('user');
    expect(ownerOf('MEMORY.md').label).toBe('agent');
    expect(ownerOf('KNOWLEDGE.md').label).toBe('gen');
  });
});
