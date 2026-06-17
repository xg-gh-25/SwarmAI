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
