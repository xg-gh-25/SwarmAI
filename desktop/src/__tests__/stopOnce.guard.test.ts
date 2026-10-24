/**
 * One Stop at a time on the Skills page (docs/design/2026-10-24-bg-agent-stop-livereload.md D3).
 *
 * What is tested: SkillsPage's own stop sets a flag before any side effect and clears
 * it when the request settles. Source guard: SkillsPage has no harness driving it.
 * The chat tab's Stop and background stop are behaviour-tested in
 * pages/chat/__tests__/turnControls.test.ts.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve, join } from 'node:path';

const SRC = resolve(__dirname, '..');
const skills = readFileSync(join(SRC, 'pages/SkillsPage.tsx'), 'utf8');

describe('one Stop at a time (Skills page)', () => {
  it('the Skills page stop is guarded', () => {
    const i = skills.search(/const handleStop = async \(\) => \{/);
    expect(i).toBeGreaterThanOrEqual(0);
    const rest = skills.slice(i);
    const sk = rest.slice(0, rest.search(/\n {2}\};\n/));
    expect(sk).toMatch(/if \(!sessionId \|\| stopInFlightRef\.current\) return;/);
    expect(sk).toMatch(/stopInFlightRef\.current = false;/);
  });
});
