/**
 * Per-tab state fields on ``UnifiedTab`` (originally Bug 3, cross-tab scroll).
 *
 * What is tested: the ``UnifiedTab`` interface in ``useUnifiedTabState.ts``
 * keeps its per-tab state fields. Methodology: read the source, extract the
 * interface body, check field names.
 *
 * Scroll is no longer tab-map state: ``scrollPosition`` was written from a ref
 * that was never attached (always undefined) and was removed 2026-10-18. Each
 * keep-mounted TabView owns its own scroll (pages/chat/scrollFocus.ts), which is
 * what keeps tabs from contaminating each other's scroll.
 *
 * **Validates: Requirements 3.6, 3.7, 3.8**
 */

import { describe, it, expect } from 'vitest';

// ===================================================================
// PRESERVATION TESTS — Tab State Integrity (Task 2)
// ===================================================================
//
// These tests verify existing per-tab state fields are present in the
// UnifiedTab interface.  They MUST PASS on unfixed code.
//
// **Validates: Requirements 3.6, 3.7, 3.8**
// ===================================================================

describe('Bug 3 Preservation — Existing per-tab state fields', () => {
  /** Shared helper: read UnifiedTab interface body from source file. */
  async function readUnifiedTabBody(): Promise<string> {
    const fs = await import('fs');
    const path = await import('path');
    const filePath = path.resolve(
      __dirname,
      '../../hooks/useUnifiedTabState.ts',
    );
    const source = fs.readFileSync(filePath, 'utf-8');
    const interfaceMatch = source.match(
      /export interface UnifiedTab\s*\{([^}]*(?:\{[^}]*\}[^}]*)*)\}/s,
    );
    expect(interfaceMatch).not.toBeNull();
    return interfaceMatch![1];
  }

  it('UnifiedTab interface includes messages field', async () => {
    expect(await readUnifiedTabBody()).toContain('messages');
  });

  it('UnifiedTab interface includes sessionId field', async () => {
    expect(await readUnifiedTabBody()).toContain('sessionId');
  });

  it('UnifiedTab interface includes pendingQuestion field', async () => {
    expect(await readUnifiedTabBody()).toContain('pendingQuestion');
  });

  it('UnifiedTab interface includes isExpanded field', async () => {
    expect(await readUnifiedTabBody()).toContain('isExpanded');
  });

  it('UnifiedTab interface includes contextWarning field', async () => {
    expect(await readUnifiedTabBody()).toContain('contextWarning');
  });
});
