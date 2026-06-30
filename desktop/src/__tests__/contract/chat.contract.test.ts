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
import { describe, it, expect, beforeAll, afterAll, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { startContractServer, type ContractServer } from './server';
import { parseSSEEvent, consumeSSEStream } from '../../services/chat';
import type { StreamEvent } from '../../types';

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

  // ── Mid-stream disconnect: drive the REAL consumeSSEStream (run_91170d96) ──
  // THE GAP THIS CLOSES: every prior "disconnect" test mocked the boundary or
  // fabricated the failure (errorHandler(new Error(...))). consumeSSEStream's
  // onDisconnect branch (chat.ts:280-285) — reader closes with NO [DONE] — had
  // ZERO coverage. This drives the REAL fn against a real fetch stream that
  // closes mid-flight, so the recurring OT01/spinner-hang class gets a gate.
  const noopStall = () => {};

  it('premature close (no [DONE]) → onDisconnect fires, onComplete does NOT', async () => {
    const response = await fetch(`${server.baseUrl}/api/chat/stream-drop`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ agent_id: 'default', message: 'test', session_id: null }),
    });
    expect(response.ok).toBe(true);
    const reader = response.body!.getReader();

    const onMessage = vi.fn<(e: StreamEvent) => void>();
    const onComplete = vi.fn();
    const onDisconnect = vi.fn();

    await consumeSSEStream(reader, noopStall, noopStall, onMessage, onComplete, onDisconnect);

    // The drop branch fired — NOT clean completion.
    expect(onDisconnect).toHaveBeenCalledTimes(1);
    expect(onComplete).not.toHaveBeenCalled();
    // Non-vacuity (Gate-1 NIT): frames actually flowed before the drop — proves
    // the stream was genuinely mid-flight, not an empty/instant close.
    expect(onMessage.mock.calls.length).toBeGreaterThanOrEqual(1);
  });

  it('INVERSE: clean stream with [DONE] → onComplete fires, onDisconnect does NOT', async () => {
    const response = await fetch(`${server.baseUrl}/api/chat/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ agent_id: 'default', message: 'test', session_id: null }),
    });
    const reader = response.body!.getReader();

    const onMessage = vi.fn<(e: StreamEvent) => void>();
    const onComplete = vi.fn();
    const onDisconnect = vi.fn();

    await consumeSSEStream(reader, noopStall, noopStall, onMessage, onComplete, onDisconnect);

    // [DONE] present → clean completion, the disconnect branch must NOT fire.
    // This inverse guard is what catches a [DONE] accidentally added to the
    // drop fixture (it would flip the test above to a false green).
    expect(onComplete).toHaveBeenCalledTimes(1);
    expect(onDisconnect).not.toHaveBeenCalled();
  });

  it('ANTI-ROT: the drop fixture must contain NO [DONE] sentinel', () => {
    // Static guard (Gate-1 NIT) — does not depend on stream timing. If someone
    // adds [DONE] to chat-stream-drop.jsonl, the premature-close test silently
    // becomes a clean-completion test (false green); this fails loudly first.
    const here = dirname(fileURLToPath(import.meta.url));
    const fixture = readFileSync(join(here, 'fixtures', 'chat-stream-drop.jsonl'), 'utf-8');
    expect(fixture).not.toContain('[DONE]');
    // And it must have ≥1 real data: frame (so the disconnect test is non-vacuous).
    expect(fixture.split('\n').filter((l) => l.startsWith('data: ')).length).toBeGreaterThanOrEqual(1);
  });
});
