/**
 * Contract Test: System Endpoints
 *
 * Verifies health, agents, and eval endpoints return expected shapes.
 * These are the endpoints the frontend checks on startup and in TopBar.
 *
 * Catches: health response shape changes, missing fields after refactor.
 *
 * @vitest-environment node
 */
import { describe, it, expect, beforeAll, afterAll } from 'vitest';
import { startContractServer, type ContractServer } from './server';

describe('System Contract', () => {
  let server: ContractServer;
  let baseUrl: string;

  beforeAll(async () => {
    server = await startContractServer();
    baseUrl = server.baseUrl;
  });

  afterAll(async () => {
    await server.stop();
  });

  it('GET /health returns healthy status with required fields', async () => {
    const res = await fetch(`${baseUrl}/health`);
    expect(res.status).toBe(200);

    const data = await res.json();
    expect(data).toHaveProperty('status', 'healthy');
    expect(data).toHaveProperty('version');
    expect(data).toHaveProperty('db_healthy');
    expect(typeof data.version).toBe('string');
    expect(typeof data.db_healthy).toBe('boolean');
  });

  it('GET /api/agents/default returns agent with required fields', async () => {
    const res = await fetch(`${baseUrl}/api/agents/default`);
    expect(res.status).toBe(200);

    const agent = await res.json();
    expect(agent).toHaveProperty('id');
    expect(agent).toHaveProperty('name');
    expect(agent).toHaveProperty('model');
    expect(agent).toHaveProperty('is_default', true);
    expect(typeof agent.id).toBe('string');
  });

  it('GET /api/eval/health returns score', async () => {
    const res = await fetch(`${baseUrl}/api/eval/health`);
    expect(res.status).toBe(200);

    const data = await res.json();
    expect(data).toHaveProperty('score');
    expect(typeof data.score).toBe('number');
  });

  it('GET /api/system/tokens/usage returns token counts', async () => {
    const res = await fetch(`${baseUrl}/api/system/tokens/usage`);
    expect(res.status).toBe(200);

    const data = await res.json();
    expect(data).toHaveProperty('today_tokens');
    expect(data).toHaveProperty('total_tokens');
    expect(typeof data.today_tokens).toBe('number');
  });

  it('health response has stable field set (no accidental removal)', async () => {
    const res = await fetch(`${baseUrl}/health`);
    const data = await res.json();

    // These fields are consumed by frontend HealthProvider
    const requiredFields = ['status', 'version', 'db_healthy'];
    for (const field of requiredFields) {
      expect(data).toHaveProperty(field);
    }
  });

  it('NEGATIVE: health fixture matches expected schema version', async () => {
    const res = await fetch(`${baseUrl}/health`);
    const data = await res.json();

    // If 'status' field ever changes from string to boolean,
    // this catches the shape drift
    expect(typeof data.status).toBe('string');
    expect(['healthy', 'degraded', 'unhealthy']).toContain(data.status);
  });
});
