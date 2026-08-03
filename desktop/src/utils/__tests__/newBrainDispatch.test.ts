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

  it('names the brain and states what it governs', () => {
    const p = buildBrainManifest('Acme Payments', 'codebase', items);
    expect(p).toContain('"Acme Payments"');
    expect(p).toContain('governs a codebase');
  });

  it('groups items GOVERN → DISTILL → SHELF, in that order', () => {
    const p = buildBrainManifest('Acme', 'codebase', items);
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
    const p = buildBrainManifest('Idea', 'idea', [
      { value: 'a research question', role: 'DISTILL' },
    ]);
    expect(p).toContain('DISTILL');
    expect(p).not.toContain('GOVERN');
    expect(p).not.toContain('SHELF');
  });

  it('a 0-material brain still produces a valid prompt (name + governs only)', () => {
    const p = buildBrainManifest('My Book', 'idea', []);
    expect(p).toContain('"My Book"');
    expect(p).toContain('just an idea');
    expect(p).not.toContain('Starter material:');
    expect(p).toContain('s_project-manager');
  });

  it('falls back to a placeholder name when blank', () => {
    const p = buildBrainManifest('   ', 'documents', []);
    expect(p).toContain('"Untitled Brain"');
  });

  it('always instructs the 6-phase setup + surfacing unreachable/conflicts', () => {
    const p = buildBrainManifest('X', 'codebase', items);
    expect(p).toContain('s_project-manager');
    expect(p.toLowerCase()).toContain('cannot reach');
    expect(p.toLowerCase()).toContain('conflict');
  });
});
