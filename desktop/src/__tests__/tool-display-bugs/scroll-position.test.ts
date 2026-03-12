/**
 * Bug condition exploration tests for cross-tab scroll contamination (Bug 3).
 *
 * What is being tested:
 * - The ``UnifiedTab`` interface in ``useUnifiedTabState.ts`` — verifying
 *   that it includes a ``scrollPosition`` field for per-tab scroll state.
 * - On unfixed code, this field does NOT exist, confirming the bug.
 *
 * Testing methodology: Type-level unit test with Vitest
 *
 * Key properties / invariants being verified:
 * - ``UnifiedTab`` interface MUST include ``scrollPosition`` field
 * - Without this field, tab switching cannot save/restore scroll state
 *
 * **Validates: Requirements 1.5, 1.6**
 *
 * CRITICAL: These tests are EXPECTED TO FAIL on unfixed code — failure
 * confirms the bugs exist. Do NOT fix the code when tests fail.
 */

import { describe, it, expect } from 'vitest';

// We import the source file to inspect the interface at runtime via
// the TypeScript compiler's type-checking. The actual runtime test
// reads the source file and checks for the scrollPosition field.
// This avoids TS compile errors while still proving the bug exists.

// ---------------------------------------------------------------------------
// Bug 3 — Cross-Tab Scroll Contamination
// ---------------------------------------------------------------------------

describe('Bug 3 — Cross-Tab Scroll Position', () => {
  it('UnifiedTab interface should include scrollPosition field', async () => {
    // Read the actual source file that defines UnifiedTab and check
    // whether scrollPosition appears in the interface definition.
    // On unfixed code, it does NOT — confirming the bug.
    //
    // This approach avoids TypeScript compile errors while still
    // proving the field is missing from the interface.
    const fs = await import('fs');
    const path = await import('path');

    const filePath = path.resolve(
      __dirname,
      '../../hooks/useUnifiedTabState.ts',
    );
    const source = fs.readFileSync(filePath, 'utf-8');

    // Extract the UnifiedTab interface block from the source
    const interfaceMatch = source.match(
      /export interface UnifiedTab\s*\{([^}]*(?:\{[^}]*\}[^}]*)*)\}/s,
    );
    expect(interfaceMatch).not.toBeNull();

    const interfaceBody = interfaceMatch![1];

    // The interface MUST contain a scrollPosition field.
    // On unfixed code, this assertion FAILS — proving Bug 3 exists.
    expect(interfaceBody).toContain('scrollPosition');
  });
});


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
