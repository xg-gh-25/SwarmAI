/**
 * Contract Test Fixture Server
 *
 * Lightweight HTTP server that serves recorded backend responses.
 * Used by contract tests to verify frontend services can parse real
 * backend response shapes without vi.mock — tests the actual HTTP layer.
 *
 * Serves: health, sessions, streaming-state, chat SSE stream.
 */
import { createServer, IncomingMessage, ServerResponse } from 'http';
import { readFileSync } from 'fs';
import { join } from 'path';
import type { AddressInfo } from 'net';

const FIXTURES_DIR = join(__dirname, 'fixtures');

function loadFixture(name: string): string {
  return readFileSync(join(FIXTURES_DIR, name), 'utf-8');
}

function sendJSON(res: ServerResponse, data: string, status = 200) {
  res.writeHead(status, {
    'Content-Type': 'application/json',
    'Access-Control-Allow-Origin': '*',
  });
  res.end(data);
}

function sendSSE(res: ServerResponse, fixtureFile: string) {
  res.writeHead(200, {
    'Content-Type': 'text/event-stream',
    'Cache-Control': 'no-cache',
    'Connection': 'keep-alive',
    'Access-Control-Allow-Origin': '*',
  });

  const lines = loadFixture(fixtureFile).split('\n');
  let i = 0;

  // Stream lines with small delays to simulate real SSE
  const interval = setInterval(() => {
    if (i >= lines.length) {
      clearInterval(interval);
      res.end();
      return;
    }
    res.write(lines[i] + '\n');
    i++;
  }, 5); // 5ms between lines — fast but realistic
}

function handleRequest(req: IncomingMessage, res: ServerResponse) {
  const url = req.url || '';
  const method = req.method || 'GET';

  // CORS preflight
  if (method === 'OPTIONS') {
    res.writeHead(204, {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, POST, DELETE',
      'Access-Control-Allow-Headers': 'Content-Type',
    });
    res.end();
    return;
  }

  // Route matching
  if (url === '/health' && method === 'GET') {
    sendJSON(res, loadFixture('health.json'));
  } else if (url === '/api/agents/default' && method === 'GET') {
    sendJSON(res, JSON.stringify({
      id: 'default',
      name: 'Swarm',
      model: 'claude-opus-4-8',
      system_prompt: null,
      allowed_tools: [],
      allowed_skills: [],
      mcp_ids: [],
      is_default: true,
    }));
  } else if (url.startsWith('/api/chat/sessions/streaming-state') && method === 'GET') {
    sendJSON(res, loadFixture('streaming-state.json'));
  } else if (url.match(/\/api\/chat\/sessions\?/) && method === 'GET') {
    sendJSON(res, loadFixture('sessions.json'));
  } else if (url === '/api/chat/sessions' && method === 'GET') {
    sendJSON(res, loadFixture('sessions.json'));
  } else if (url.match(/\/api\/chat\/sessions\/[^/]+$/) && method === 'DELETE') {
    res.writeHead(204, { 'Access-Control-Allow-Origin': '*' });
    res.end();
  } else if (url === '/api/chat/stream' && method === 'POST') {
    sendSSE(res, 'chat-stream.jsonl');
  } else if (url === '/api/chat/stream-drop' && method === 'POST') {
    // Premature mid-stream close: a few real data: frames then res.end()
    // WITHOUT a [DONE] sentinel (sendSSE always res.end()s at fixture end).
    // Drives consumeSSEStream's onDisconnect branch (chat.ts:280-285).
    sendSSE(res, 'chat-stream-drop.jsonl');
  } else if (url === '/api/eval/health' && method === 'GET') {
    sendJSON(res, JSON.stringify({ score: 85, dimensions: {} }));
  } else if (url === '/api/system/tokens/usage' && method === 'GET') {
    sendJSON(res, JSON.stringify({ today_tokens: 1200000, total_tokens: 45800000 }));
  } else {
    res.writeHead(404, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ error: 'Not found', path: url }));
  }
}

// ── Server lifecycle for vitest ──
// Each test file gets its own server instance to avoid parallel test conflicts.

export interface ContractServer {
  baseUrl: string;
  stop: () => Promise<void>;
}

export async function startContractServer(): Promise<ContractServer> {
  return new Promise((resolve) => {
    const srv = createServer(handleRequest);
    srv.listen(0, '127.0.0.1', () => {
      const addr = srv.address() as AddressInfo;
      const url = `http://127.0.0.1:${addr.port}`;
      resolve({
        baseUrl: url,
        stop: () => new Promise<void>((res) => srv.close(() => res())),
      });
    });
  });
}

// Legacy exports for simpler test API (backwards-compat)
let _lastServer: ContractServer | null = null;

export function getContractBaseUrl(): string {
  return _lastServer?.baseUrl || '';
}

export async function createAndStart(): Promise<string> {
  _lastServer = await startContractServer();
  return _lastServer.baseUrl;
}

export async function stopContractServer(): Promise<void> {
  if (_lastServer) {
    await _lastServer.stop();
    _lastServer = null;
  }
}
