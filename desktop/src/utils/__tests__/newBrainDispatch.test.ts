/**
 * Tests for newBrainDispatch — the pure launcher logic (classify + manifest).
 * Property-ish: classification is a total function of TYPE; the manifest groups
 * by role, omits empty groups, and always yields a valid prompt.
 */
import { describe, it, expect } from 'vitest';
import {
  classifyStarterItem,
  buildBrainManifest,
  detectKind,
  previewSections,
  validateBrainName,
  buildAddKnowledgePrompt,
  buildResumeSetupPrompt,
  type StarterItem,
} from '../newBrainDispatch';

describe('detectKind — type from raw value', () => {
  it('git URLs and .git dirs → repo', () => {
    expect(detectKind('github.com/acme/payments')).toBe('repo');
    expect(detectKind('git@github.com:acme/x.git')).toBe('repo');
    expect(detectKind('/Users/me/proj/.git')).toBe('repo');
  });
  it('http(s) links → link', () => {
    expect(detectKind('https://notion.so/acme/roadmap')).toBe('link');
    expect(detectKind('www.example.com/spec')).toBe('link');
  });
  it('trailing-slash paths → folder', () => {
    expect(detectKind('~/work/acme/compliance-notes/')).toBe('folder');
  });
  it('doc-extension files → file', () => {
    expect(detectKind('architecture-decisions.pdf')).toBe('file');
    expect(detectKind('/abs/path/notes.md')).toBe('file');
  });
  it('bare paths with no doc extension → folder (safe default)', () => {
    expect(detectKind('/Users/me/some/dir')).toBe('folder');
  });
  it('free text → text', () => {
    expect(detectKind('build a payments reconciliation system')).toBe('text');
  });
  it('Windows paths → folder/file, not text (Gate-2 #1)', () => {
    expect(detectKind('C:\\Users\\me\\project')).toBe('folder');
    expect(detectKind('C:\\Users\\me\\notes.md')).toBe('file');
    expect(detectKind('\\\\server\\share\\docs')).toBe('folder');
  });
});

describe('classifyStarterItem — role BY TYPE (no content)', () => {
  it('repo → GOVERN', () => {
    expect(classifyStarterItem({ value: 'github.com/acme/payments' })).toBe('GOVERN');
    expect(classifyStarterItem({ value: 'x', kind: 'repo' })).toBe('GOVERN');
  });
  it('doc file / link / pasted text → DISTILL', () => {
    expect(classifyStarterItem({ value: 'decisions.pdf' })).toBe('DISTILL');
    expect(classifyStarterItem({ value: 'https://notion.so/x' })).toBe('DISTILL');
    expect(classifyStarterItem({ value: 'some pasted requirement note' })).toBe('DISTILL');
  });
  it('folder → SHELF (the safe default, XG-confirmed)', () => {
    expect(classifyStarterItem({ value: '~/work/acme/notes/' })).toBe('SHELF');
    expect(classifyStarterItem({ value: 'x', kind: 'folder' })).toBe('SHELF');
  });
  it('caller kind hint overrides value detection', () => {
    // A dropped OS folder whose name happens to end in .pdf is still a folder.
    expect(classifyStarterItem({ value: 'weird.pdf', kind: 'folder' })).toBe('SHELF');
  });
});

describe('buildBrainManifest', () => {
  const items: StarterItem[] = [
    { value: 'github.com/acme/payments', role: 'GOVERN' },
    { value: 'decisions.pdf', role: 'DISTILL' },
    { value: 'https://notion.so/roadmap', role: 'DISTILL' },
    { value: '~/work/acme/notes/', role: 'SHELF' },
  ];

  it('names the brain and counts its assets from the GOVERN items', () => {
    const p = buildBrainManifest('Acme Payments', items);
    expect(p).toContain('"Acme Payments"');
    expect(p).toContain('it governs 1 asset');
  });

  it('groups items GOVERN → DISTILL → SHELF, in that order', () => {
    const p = buildBrainManifest('Acme', items);
    const gi = p.indexOf('GOVERN');
    const di = p.indexOf('DISTILL');
    const si = p.indexOf('SHELF');
    expect(gi).toBeGreaterThan(-1);
    expect(gi).toBeLessThan(di);
    expect(di).toBeLessThan(si);
    expect(p).toContain('- github.com/acme/payments');
    expect(p).toContain('- decisions.pdf');
    expect(p).toContain('- ~/work/acme/notes/');
  });

  it('omits empty role groups', () => {
    const p = buildBrainManifest('Idea', [
      { value: 'a research question', role: 'DISTILL' },
    ]);
    expect(p).toContain('DISTILL');
    expect(p).not.toContain('GOVERN');
    expect(p).not.toContain('SHELF');
  });

  it('a 0-material brain is a pure-knowledge brain and still a valid prompt', () => {
    const p = buildBrainManifest('My Book', []);
    expect(p).toContain('"My Book"');
    expect(p).toContain('pure-knowledge brain');
    expect(p).not.toContain('Starter material:');
    expect(p).toContain('s_project-manager');
  });

  it('falls back to a placeholder name when blank', () => {
    const p = buildBrainManifest('   ', []);
    expect(p).toContain('"Untitled Brain"');
  });

  it('asks for the generated Project Agent, unreachable/conflicts, and the ready hand-off', () => {
    const p = buildBrainManifest('X', items);
    expect(p).toContain('s_project-manager');
    expect(p).toContain('P3b');
    expect(p).toContain('project-agent.yaml');
    expect(p).toContain('pipeline profiles');
    expect(p.toLowerCase()).toContain('cannot reach');
    expect(p.toLowerCase()).toContain('conflict');
    expect(p).toContain('Brain Hub');
  });
});

describe('buildAddKnowledgePrompt / buildResumeSetupPrompt', () => {
  it('routes each role to the skill that owns the write, and ends with P6 --record', () => {
    const p = buildAddKnowledgePrompt(' Acme ', [
      { value: '~/docs/runbook.md', role: 'DISTILL' },
      { value: '~/notes/', role: 'SHELF' },
      { value: 'github.com/acme/billing', role: 'GOVERN' },
    ]);
    expect(p).toContain('"Acme" Brain');
    expect(p.indexOf('GOVERN')).toBeLessThan(p.indexOf('DISTILL'));
    expect(p).toContain('- ~/docs/runbook.md');
    for (const s of ['s_ddd-persist', 's_learn-content', 's_library', 'proposal for me to review', '--record']) expect(p).toContain(s);
  });
  it('a resume verifies first and redoes nothing', () => {
    const p = buildResumeSetupPrompt('Acme');
    expect(p).toContain('P6 check first');
    expect(p).toContain('Do not redo');
    expect(p).toContain('--record');
  });
});

describe('validateBrainName — the backend rule, checked before dispatch', () => {
  it('accepts what create_project accepts', () => {
    for (const n of ['Acme Payments', 'payments-api', 'a', 'v2.1_core']) expect(validateBrainName(n)).toBeNull();
    expect(validateBrainName('')).toBeNull();
  });
  it('refuses bad characters, a bad first character, reserved names and duplicates', () => {
    expect(validateBrainName('支付')).toContain('Use letters');
    expect(validateBrainName('-lead')).toContain('Use letters');
    expect(validateBrainName('x'.repeat(101))).toContain('Use letters');
    expect(validateBrainName('con')).toContain('reserved');
    expect(validateBrainName('Com1.txt')).toContain('reserved');
    expect(validateBrainName('SWARMAI', ['SwarmAI'])).toContain('already exists');
    for (const n of ['General', 'general', 'GENERAL']) expect(validateBrainName(n)).toContain('built-in General Brain');
  });
});

describe('previewSections', () => {
  it('always creates Identity, Capabilities and Delivery; Knowledge needs something to read', () => {
    expect(previewSections([])).toEqual({ identity: true, knowledge: false, gates: false, capabilities: true, delivery: true, refresher: false });
    expect(previewSections([{ role: 'DISTILL' }]).knowledge).toBe(true);
    expect(previewSections([{ role: 'SHELF' }]).knowledge).toBe(false);
  });
  it('a governed asset also fills Gates and the Refresher', () => {
    const p = previewSections([{ role: 'GOVERN' }]);
    expect(p.gates && p.refresher && p.knowledge).toBe(true);
  });
});
