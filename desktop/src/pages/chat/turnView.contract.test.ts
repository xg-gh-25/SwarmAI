/**
 * Layer-4 contract: the step-label adapter vs the REAL backend summaries.
 *
 * `__fixtures__/tool-summaries.json` is generated FROM
 * `backend/core/tool_summarizer.py` and kept in lockstep by
 * `backend/tests/test_tool_summary_fixture.py` (red there when the backend
 * wording changes). Here every real summary must map to the verb the UI promises
 * — so a backend rewording that the adapter no longer understands goes red on one
 * side or the other, never silently degrades to raw text.
 */
import { describe, it, expect } from 'vitest';
import fixture from './__fixtures__/tool-summaries.json';
import { stepLabel } from './turnView';

type Row = { name: string; category: string; summary: string; expectedVerb: string };

describe('stepLabel × real backend summaries', () => {
  it('the fixture is non-empty (an empty contract would pass vacuously)', () => {
    expect((fixture as Row[]).length).toBeGreaterThanOrEqual(15);
  });

  it.each((fixture as Row[]).map((r) => [r.name, r.summary, r.expectedVerb, r.category] as const))(
    '%s — %s → %s',
    (name, summary, expectedVerb, category) => {
      const label = stepLabel({ type: 'tool_use', id: 'x', name, summary, category });
      expect(label.verb).toBe(expectedVerb);
      // No label may carry the leading `cd <path>` of a Bash command.
      expect(label.object).not.toMatch(/^cd\s/);
      expect(label.object).not.toContain('/Users/x/repo &&');
    },
  );
});
