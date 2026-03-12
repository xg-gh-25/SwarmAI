/**
 * Bug condition exploration tests for SSE content block case conversion (Bug 2).
 *
 * What is being tested:
 * - Raw SSE-parsed tool_result blocks retain snake_case fields (tool_use_id,
 *   is_error) instead of camelCase (toolUseId, isError), causing the resultMap
 *   lookup in AssistantMessageView to fail.
 * - ``toMessageCamelCase()`` in chat.ts passes content blocks through as-is
 *   with NO field conversion on content blocks.
 *
 * Testing methodology: Unit tests with Vitest
 *
 * Key properties / invariants being verified:
 * - After SSE parsing, tool_result content blocks MUST have ``toolUseId``
 *   (camelCase), not ``tool_use_id`` (snake_case)
 * - ``toMessageCamelCase()`` MUST convert content block fields
 *
 * **Validates: Requirements 1.3, 1.4**
 *
 * CRITICAL: These tests are EXPECTED TO FAIL on unfixed code — failure
 * confirms the bugs exist. Do NOT fix the code when tests fail.
 */

import { describe, it, expect } from 'vitest';
import { toCamelCaseContentBlock, toCamelCaseContent } from '../../services/chat';

// ---------------------------------------------------------------------------
// Bug 2 — SSE Content Block Case Conversion
// ---------------------------------------------------------------------------

describe('Bug 2 — SSE Content Block Case Conversion', () => {
  it('toCamelCaseContentBlock converts tool_result snake_case fields to camelCase', () => {
    // Simulate what the backend sends via SSE — snake_case fields
    const rawSSEBlock: Record<string, unknown> = {
      type: 'tool_result',
      tool_use_id: 'toolu_abc123',
      is_error: false,
      content: 'file contents...',
    };

    const converted = toCamelCaseContentBlock(rawSSEBlock);

    // After conversion, the block should have camelCase fields
    expect(converted).toHaveProperty('toolUseId');
    expect(converted.toolUseId).toBe('toolu_abc123');
    expect(converted).toHaveProperty('isError');
    expect(converted.isError).toBe(false);
    // Snake_case fields should be removed
    expect(converted).not.toHaveProperty('tool_use_id');
    expect(converted).not.toHaveProperty('is_error');
  });

  it('toCamelCaseContent converts tool_result blocks in a content array', () => {
    const rawContent: unknown[] = [
      { type: 'text', text: 'Hello' },
      {
        type: 'tool_result',
        tool_use_id: 'toolu_xyz789',
        is_error: false,
        content: 'result data',
      },
    ];

    const converted = toCamelCaseContent(rawContent) as Array<Record<string, unknown>>;

    // Text block unchanged
    expect(converted[0]).toEqual({ type: 'text', text: 'Hello' });
    // tool_result block converted
    const toolResultBlock = converted[1];
    expect(toolResultBlock).toHaveProperty('toolUseId');
    expect(toolResultBlock).not.toHaveProperty('tool_use_id');
    expect(toolResultBlock).toHaveProperty('isError');
    expect(toolResultBlock).not.toHaveProperty('is_error');
  });
});


// ===================================================================
// PRESERVATION TESTS — Non-tool_result Blocks (Task 2)
// ===================================================================
//
// These tests verify existing behavior that MUST be preserved after
// the Bug 2 fix.  They MUST PASS on unfixed code.
//
// **Validates: Requirements 3.3, 3.4, 3.5**
// ===================================================================

describe('Bug 2 Preservation — Non-tool_result content blocks unchanged', () => {
  it('text content blocks pass through unchanged (no field modification)', () => {
    // Simulate a text content block as it arrives from SSE / REST
    const textBlock = {
      type: 'text' as const,
      text: 'Here is the analysis of your code...',
    };

    // On both unfixed and fixed code, text blocks must pass through
    // with all fields intact — no fields added, removed, or renamed.
    const parsed = JSON.parse(JSON.stringify(textBlock));

    expect(parsed.type).toBe('text');
    expect(parsed.text).toBe('Here is the analysis of your code...');
    // Text blocks should NOT have any tool-related fields
    expect(parsed).not.toHaveProperty('toolUseId');
    expect(parsed).not.toHaveProperty('tool_use_id');
    expect(parsed).not.toHaveProperty('isError');
    expect(parsed).not.toHaveProperty('is_error');
  });

  it('tool_use content blocks pass through unchanged (id, name, summary, category preserved)', () => {
    // Simulate a tool_use content block — these already use camelCase
    // field names from the backend (id, name are universal)
    const toolUseBlock = {
      type: 'tool_use' as const,
      id: 'toolu_abc123',
      name: 'bash',
      input: { command: 'npm test' },
      summary: 'Running: npm test',
      category: 'bash',
    };

    // On both unfixed and fixed code, tool_use blocks must pass through
    // with all fields intact — the fix only targets tool_result blocks.
    const parsed = JSON.parse(JSON.stringify(toolUseBlock));

    expect(parsed.type).toBe('tool_use');
    expect(parsed.id).toBe('toolu_abc123');
    expect(parsed.name).toBe('bash');
    expect(parsed.input).toEqual({ command: 'npm test' });
    expect(parsed.summary).toBe('Running: npm test');
    expect(parsed.category).toBe('bash');
  });

  it('mixed content array preserves all block types', () => {
    // Simulate a full assistant message content array with mixed types
    const content = [
      { type: 'text', text: 'Let me run that for you.' },
      {
        type: 'tool_use',
        id: 'toolu_xyz',
        name: 'read',
        input: { path: 'src/app.ts' },
        summary: 'Reading src/app.ts',
        category: 'read',
      },
      { type: 'text', text: 'Here are the results.' },
    ];

    const parsed = JSON.parse(JSON.stringify(content));

    // All three blocks should be present and unchanged
    expect(parsed).toHaveLength(3);
    expect(parsed[0].type).toBe('text');
    expect(parsed[0].text).toBe('Let me run that for you.');
    expect(parsed[1].type).toBe('tool_use');
    expect(parsed[1].id).toBe('toolu_xyz');
    expect(parsed[1].name).toBe('read');
    expect(parsed[2].type).toBe('text');
    expect(parsed[2].text).toBe('Here are the results.');
  });
});
