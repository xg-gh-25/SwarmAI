/** The overlay's explanatory text describes mechanisms only (docs/design/2026-10-26-eval-as-product.md
 *  §3.6): no run times, weekdays or cadences. The schedule lives in the backend and its next run
 *  is read from /api/eval/health; a sentence like "runs every day" goes stale the day the
 *  schedule changes, and the quick check runs after a session ends, never "once a day".
 *
 *  This reads every tab's source, so it covers text that renders only in some states. The
 *  sparklines are bucketed by week (the shape of the data, not when anything runs), so
 *  "weekly points" and "weekly history" are allowed. */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const DIR = join(dirname(fileURLToPath(import.meta.url)), '..');
const CADENCE = [
  /every day|each day|every (two )?weeks?|\bdaily\b|biweekly|first session/i,
  /\bweekly(?! (points|history))/i,
  /Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday/,
  /每天|每日|每两周|两周一次|每周一次|每周抽查|周[一二三四五六日]|星期/,
  /\b\d{1,2}:\d{2}\b/,   // a clock time
];

const sources = readdirSync(DIR).filter((f) => f.endsWith('.tsx'))
  .map((f) => ({ f, text: readFileSync(join(DIR, f), 'utf8').split('\n').filter((l) => !/^\s*(\/\/|\*|\/\*\*)/.test(l)).join('\n') }));

describe('Eval overlay copy', () => {
  it('reads all six tabs', () => {
    for (const tab of ['OverviewTab', 'GoldenCasesTab', 'OnlineTab', 'OfflineTab', 'JudgesTab', 'GuideTab'])
      expect(sources.map((s) => s.f)).toContain(`${tab}.tsx`);
  });

  it.each(CADENCE.map((re) => [String(re), re] as const))('writes no cadence or time: %s', (_name, re) => {
    const hits = sources.flatMap(({ f, text }) => (text.match(new RegExp(re.source, re.flags + 'g')) ?? []).map((m) => `${f}: ${m}`));
    expect(hits).toEqual([]);
  });
});
