/**
 * Contract Test: Chat SSE Stream
 *
 * Verifies that the frontend's SSE parsing logic (parseSSEEvent,
 * consumeSSEStream pattern) correctly handles real backend response shapes.
 *
 * NOT a unit test (no vi.mock). Uses a real HTTP fixture server.
 * Catches: API shape drift, SSE parse errors, response format changes.
 *
 * @vitest-environment node
 */
import { describe, it, expect, beforeAll, afterAll } from 'vitest';
import { startContractServer, type ContractServer } from './server';
import { parseSSEEvent } from '../../services/chat';

describe('Chat SSE Stream Contract', () => {
  let server: ContractServer;

  beforeAll(async () => {
    server = await startContractServer();
  });

  afterAll(async () => {
    await server.stop();
  });

  it('streams chat events and receives result event', async () => {
    const baseUrl = server.baseUrl;
    const events: Array<{ type: string; session_id?: string; content?: unknown[] }> = [];

    const response = await fetch(`${baseUrl}/api/chat/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        agent_id: 'default',
        message: 'test',
        session_id: null,
      }),
    });

    expect(response.ok).toBe(true);
    expect(response.headers.get('content-type')).toContain('text/event-stream');

    const reader = response.body!.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let done = false;

    while (!done) {
      const { done: streamDone, value } = await reader.read();
      if (streamDone) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const data = line.slice(6);
          if (data === '[DONE]') { done = true; break; }
          try {
            const event = JSON.parse(data);
            events.push(event);
            if (event.type === 'result') done = true;
          } catch { /* incomplete */ }
        }
      }
    }

    // Verify we got the expected event types
    expect(events.length).toBeGreaterThanOrEqual(2);
    expect(events.some(e => e.type === 'assistant')).toBe(true);
    expect(events.some(e => e.type === 'result')).toBe(true);

    // Verify session_id is present in events
    const resultEvent = events.find(e => e.type === 'result')!;
    expect(resultEvent.session_id).toBeDefined();
    expect(typeof resultEvent.session_id).toBe('string');
  });

  it('parseSSEEvent handles assistant event with content blocks', () => {
    const raw = '{"type":"assistant","content":[{"type":"text","text":"hello"}],"session_id":"s1"}';
    const event = parseSSEEvent(raw);

    expect(event.type).toBe('assistant');
    expect(event.content).toBeDefined();
    expect(Array.isArray(event.content)).toBe(true);
    expect(event.content![0]).toHaveProperty('type', 'text');
  });

  it('parseSSEEvent handles result event with usage metrics', () => {
    const raw = JSON.stringify({
      type: 'result',
      subtype: '',
      stop_reason: 'end_turn',
      session_id: 'test-001',
      duration_ms: 5000,
      total_cost_usd: 0.01,
      num_turns: 3,
      usage: {
        input_tokens: 10000,
        output_tokens: 500,
        cache_read_input_tokens: 9000,
        cache_creation_input_tokens: 1000,
      },
    });
    const event = parseSSEEvent(raw);

    expect(event.type).toBe('result');
    expect(event.sessionId || (event as Record<string, unknown>).session_id).toBeDefined();
  });

  it('parseSSEEvent handles tool_use event with camelCase conversion', () => {
    const raw = JSON.stringify({
      type: 'assistant',
      content: [
        { type: 'tool_use', id: 'tu_1', name: 'Read', input: { file_path: '/tmp/x' }, summary: 'Reading file' },
        { type: 'tool_result', tool_use_id: 'tu_1', content: 'file contents', is_error: false },
      ],
    });
    const event = parseSSEEvent(raw);

    expect(event.content).toBeDefined();
    const blocks = event.content as Array<Record<string, unknown>>;
    // tool_result should have camelCase conversions
    const toolResult = blocks.find(b => b.type === 'tool_result');
    expect(toolResult).toBeDefined();
    expect(toolResult!.toolUseId).toBe('tu_1');
    expect(toolResult!.isError).toBe(false);
    // Original snake_case keys should be removed
    expect(toolResult!.tool_use_id).toBeUndefined();
    expect(toolResult!.is_error).toBeUndefined();
  });

  it('NEGATIVE: corrupt JSON in SSE data throws on parse', () => {
    expect(() => parseSSEEvent('{invalid json')).toThrow();
  });

  it('NEGATIVE: missing type field still parses (no crash)', () => {
    const raw = '{"content":[{"type":"text","text":"hi"}]}';
    const event = parseSSEEvent(raw);
    // Should not crash — type may be undefined but parsing works
    expect(event).toBeDefined();
  });
});
