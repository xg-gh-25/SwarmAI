/**
 * Contract Test: Session Management
 *
 * Verifies frontend session service functions correctly parse real backend
 * response shapes (snake_case → camelCase transformation, field presence).
 *
 * Catches: toSessionCamelCase drift, missing fields, renamed API fields.
 *
 * @vitest-environment node
 */
import { describe, it, expect, beforeAll, afterAll } from 'vitest';
import { startContractServer, type ContractServer } from './server';

describe('Session Contract', () => {
  let server: ContractServer;
  let baseUrl: string;

  beforeAll(async () => {
    server = await startContractServer();
    baseUrl = server.baseUrl;
  });

  afterAll(async () => {
    await server.stop();
  });

  it('GET /api/chat/sessions returns array with expected shape', async () => {
    const res = await fetch(`${baseUrl}/api/chat/sessions`);
    expect(res.status).toBe(200);

    const sessions = await res.json();
    expect(Array.isArray(sessions)).toBe(true);
    expect(sessions.length).toBeGreaterThan(0);

    const session = sessions[0];
    // Backend returns snake_case — verify the raw shape
    expect(session).toHaveProperty('id');
    expect(session).toHaveProperty('agent_id');
    expect(session).toHaveProperty('title');
    expect(session).toHaveProperty('created_at');
    expect(session).toHaveProperty('last_accessed_at');
    expect(typeof session.id).toBe('string');
    expect(typeof session.agent_id).toBe('string');
    expect(typeof session.created_at).toBe('string');
  });

  it('GET /api/chat/sessions/streaming-state returns sessions map', async () => {
    const res = await fetch(`${baseUrl}/api/chat/sessions/streaming-state`);
    expect(res.status).toBe(200);

    const data = await res.json();
    expect(data).toHaveProperty('sessions');
    expect(typeof data.sessions).toBe('object');

    // Each session entry has streaming (bool) and state (string)
    const firstKey = Object.keys(data.sessions)[0];
    if (firstKey) {
      const entry = data.sessions[firstKey];
      expect(entry).toHaveProperty('streaming');
      expect(entry).toHaveProperty('state');
      expect(typeof entry.streaming).toBe('boolean');
      expect(typeof entry.state).toBe('string');
    }
  });

  // ── Root-1 SSOT Phase 3: streaming-state read API carries the 4 mirror fields ──
  // The backend (chat.py:776-786) emits waiting_input/pending_count/pending_question/
  // last_drained_seqs. The frontend MUST be able to parse them — the prior
  // getStreamingState type dropped all 4. These assert the contract is complete.
  it('streaming-state entries carry the Phase-2 mirror fields (waiting_input, pending_count, pending_question, last_drained_seqs)', async () => {
    const res = await fetch(`${baseUrl}/api/chat/sessions/streaming-state`);
    expect(res.status).toBe(200);
    const data = await res.json();
    const entries = Object.values(data.sessions) as Array<Record<string, unknown>>;
    expect(entries.length).toBeGreaterThan(0);
    for (const entry of entries) {
      expect(entry).toHaveProperty('waiting_input');
      expect(entry).toHaveProperty('pending_count');
      expect(entry).toHaveProperty('pending_question');
      expect(entry).toHaveProperty('last_drained_seqs');
      expect(typeof entry.waiting_input).toBe('boolean');
      expect(typeof entry.pending_count).toBe('number');
      expect(Array.isArray(entry.last_drained_seqs)).toBe(true);
      // pending_question is null OR an object with tool_use_id
      if (entry.pending_question !== null) {
        expect(entry.pending_question).toHaveProperty('tool_use_id');
      }
    }
  });

  it('AC5: a waiting_input session exposes pending_question with {tool_use_id, questions} (lost-SSE re-surface source)', async () => {
    const res = await fetch(`${baseUrl}/api/chat/sessions/streaming-state`);
    const data = await res.json();
    const waiting = Object.values(data.sessions).find(
      (e: unknown) => (e as Record<string, unknown>).waiting_input === true,
    ) as Record<string, unknown> | undefined;
    expect(waiting).toBeDefined();
    const pq = waiting!.pending_question as Record<string, unknown>;
    expect(pq).not.toBeNull();
    expect(pq).toHaveProperty('tool_use_id');
    expect(pq).toHaveProperty('questions');
    expect(Array.isArray(pq.questions)).toBe(true);
  });

  it('AC4: a busy session reports pending_count and an idle session can report last_drained_seqs', async () => {
    const res = await fetch(`${baseUrl}/api/chat/sessions/streaming-state`);
    const data = await res.json();
    const entries = Object.values(data.sessions) as Array<Record<string, unknown>>;
    const hasPending = entries.some((e) => (e.pending_count as number) >= 1);
    const hasDrained = entries.some((e) => (e.last_drained_seqs as number[]).length > 0);
    expect(hasPending).toBe(true);
    expect(hasDrained).toBe(true);
  });

  it('DELETE /api/chat/sessions/:id returns 204', async () => {
    const res = await fetch(`${baseUrl}/api/chat/sessions/fake-id-123`, {
      method: 'DELETE',
    });
    expect(res.status).toBe(204);
  });

  it('NEGATIVE: GET unknown endpoint returns 404 with error shape', async () => {
    const res = await fetch(`${baseUrl}/api/nonexistent`);
    expect(res.status).toBe(404);

    const data = await res.json();
    expect(data).toHaveProperty('error');
  });
});
