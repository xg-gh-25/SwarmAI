/**
 * Tests for isBookkeepingPath — the Canvas output-list filter that separates
 * real user deliverables from agent bookkeeping. This is the one genuinely-new
 * bit of logic vs Radar's ChangesSection (which shows all written files).
 *
 * Non-vacuous: each "kept" case is a real deliverable shape; each "dropped"
 * case is a real bookkeeping shape seen in production runs.
 */
import { describe, it, expect } from 'vitest';
import { isBookkeepingPath } from '../CanvasOutputRail';

describe('isBookkeepingPath', () => {
  it('KEEPS real deliverables', () => {
    for (const p of [
      'Knowledge/Designs/2026-08-02-canvas-blueprint.md',
      '/Users/gawan/.swarm-ai/SwarmWS/Knowledge/Reports/x.md',
      'desktop/src/components/file-viewer/CanvasOutputRail.tsx',
      'backend/core/pipeline_profiles.py',
      'report.html',
    ]) {
      expect(isBookkeepingPath(p), p).toBe(false);
    }
  });

  it('DROPS .artifacts/ pipeline records (any depth)', () => {
    for (const p of [
      'Projects/SwarmAI/.artifacts/runs/run_a5ec4b6a/run.json',
      '.artifacts/runs/x/checkpoint-1.json',
      '/abs/Projects/P/.artifacts/METRICS.json',
    ]) {
      expect(isBookkeepingPath(p), p).toBe(true);
    }
  });

  it('DROPS dotfiles / dot-directories', () => {
    for (const p of [
      '.context/MEMORY.md',
      'foo/.git/config',
      '.DS_Store',
      'a/b/.eslintrc.json',
    ]) {
      expect(isBookkeepingPath(p), p).toBe(true);
    }
  });

  it('DROPS temp / scratch paths', () => {
    for (const p of ['/tmp/canvas_plan.json', '/private/tmp/x.json', 'foo/scratch.tmp', 'bar/file~']) {
      expect(isBookkeepingPath(p), p).toBe(true);
    }
  });

  it('DROPS empty / falsy path (fail-closed)', () => {
    expect(isBookkeepingPath('')).toBe(true);
  });

  it('does NOT misclassify a leading slash (absolute path) as a dotfile', () => {
    // split('/')[0] === '' for absolute paths — must not be treated as a dot-dir
    expect(isBookkeepingPath('/Users/gawan/Knowledge/x.md')).toBe(false);
  });
});
