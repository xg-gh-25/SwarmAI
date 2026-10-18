/**
 * Tests for file editor bug fixes:
 * 1. Copy Path → absolute (FileContextMenu)
 * 3. Spaced paths accepted (MarkdownRenderer)
 */
import { describe, it, expect, beforeAll } from 'vitest';

// We test the exported utility functions directly
// Import will work after the fixes are applied

describe('Bug 1: isWorkspaceFilePath accepts paths with 1-2 spaces', () => {
  // Dynamically import to get the live version
  let isWorkspaceFilePath: (text: string) => boolean;

  beforeAll(async () => {
    const mod = await import('../components/common/MarkdownRenderer');
    isWorkspaceFilePath = mod.isWorkspaceFilePath;
  });

  it('accepts path with one space (has / and extension)', () => {
    expect(isWorkspaceFilePath('~/My Projects/file.py')).toBe(true);
  });

  it('accepts path with two spaces (has / and extension)', () => {
    expect(isWorkspaceFilePath('~/My Cool Project/main.ts')).toBe(true);
  });

  it('rejects prose with 3+ spaces (sentence)', () => {
    expect(isWorkspaceFilePath('this is a regular sentence.txt')).toBe(false);
  });

  it('rejects two-word backtick text without path structure', () => {
    // Adversarial finding: "my variable" or "use strict" should NOT become clickable
    expect(isWorkspaceFilePath('my variable')).toBe(false);
    expect(isWorkspaceFilePath('use strict')).toBe(false);
    expect(isWorkspaceFilePath('npm install')).toBe(false);
  });

  it('rejects spaced text with extension but no slash', () => {
    // "some file.txt" — no path separator, likely prose
    expect(isWorkspaceFilePath('some file.txt')).toBe(false);
  });

  it('still accepts no-space paths', () => {
    expect(isWorkspaceFilePath('backend/core/session_unit.py')).toBe(true);
  });

  it('still rejects URLs', () => {
    expect(isWorkspaceFilePath('https://example.com/file.txt')).toBe(false);
  });
});
