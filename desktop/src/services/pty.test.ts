/**
 * Tests for the vendored PTY service (services/pty.ts).
 *
 * The service wraps our app-level Rust commands (pty_spawn/pty_read/pty_write/
 * pty_resize/pty_kill) into an xterm-friendly IPty interface with a read poll
 * loop. We mock ONLY the Tauri `invoke` boundary (system boundary) — everything
 * else is real service code (Boundary-Only mock rule).
 *
 * Covers Gate-1 findings: H2 (Uint8Array→string decode), M1 (onData listener
 * disposal), AC1 (data streams via poll loop), AC2 (write), AC6 (kill).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// Mock the Tauri invoke boundary. Each test wires its own behavior.
const invokeMock = vi.fn();
vi.mock('@tauri-apps/api/core', () => ({
  invoke: (...args: unknown[]) => invokeMock(...args),
}));

import { spawn } from './pty';

describe('pty service', () => {
  beforeEach(() => {
    invokeMock.mockReset();
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('AC1: spawn calls pty_spawn with file/args/cwd and returns an IPty', async () => {
    invokeMock.mockImplementation((cmd: string) => {
      if (cmd === 'pty_spawn') return Promise.resolve(7);
      // pty_read blocks forever (no data) so the poll loop idles
      if (cmd === 'pty_read') return new Promise(() => {});
      return Promise.resolve();
    });

    const pty = spawn('/bin/zsh', ['-l'], { cols: 80, rows: 24, cwd: '/tmp/proj' });
    // spawn is fire-and-forget on the handle; give the microtask queue a tick
    await new Promise((r) => setTimeout(r, 0));

    expect(invokeMock).toHaveBeenCalledWith('pty_spawn', expect.objectContaining({
      file: '/bin/zsh',
      args: ['-l'],
      cols: 80,
      rows: 24,
      cwd: '/tmp/proj',
    }));
    expect(typeof pty.onData).toBe('function');
    expect(typeof pty.write).toBe('function');
    expect(typeof pty.resize).toBe('function');
    expect(typeof pty.kill).toBe('function');
    pty.kill();
  });

  it('AC1 + H2: read poll loop decodes Uint8Array bytes to a string and emits onData', async () => {
    // Rust returns raw bytes (Tauri IPC binary Response → Uint8Array in JS).
    // "héllo" in UTF-8 — includes a multi-byte char to prove decode (not naive).
    const bytes = new TextEncoder().encode('héllo');
    let readCount = 0;
    invokeMock.mockImplementation((cmd: string) => {
      if (cmd === 'pty_spawn') return Promise.resolve(1);
      if (cmd === 'pty_read') {
        readCount += 1;
        if (readCount === 1) return Promise.resolve(bytes);
        // second read → EOF stops the loop
        return Promise.reject('EOF');
      }
      return Promise.resolve();
    });

    const received: string[] = [];
    const pty = spawn('/bin/zsh', [], { cols: 80, rows: 24 });
    pty.onData((d) => received.push(d));

    // wait for the poll loop to deliver + hit EOF
    await vi.waitFor(() => expect(received.join('')).toBe('héllo'), { timeout: 1000 });
    // it must be a decoded STRING, not a Uint8Array (H2)
    expect(typeof received[0]).toBe('string');
  });

  it('AC1 REGRESSION: read decodes an ArrayBuffer (the REAL Tauri shape) — not empty', async () => {
    // The bug: `Uint8Array.from(anArrayBuffer)` returns an EMPTY array, so the
    // terminal rendered NOTHING. tauri::ipc::Response arrives as an ArrayBuffer,
    // NOT a Uint8Array — so the old `instanceof Uint8Array ? chunk : from(chunk)`
    // hit the broken from() branch and dropped every byte. This test feeds an
    // ArrayBuffer (the production shape) and asserts the bytes actually arrive.
    // Mutation-guard: revert to Uint8Array.from(chunk) → received is '' → RED.
    const src = new TextEncoder().encode('hi$ ');
    const ab: ArrayBuffer = src.buffer.slice(0); // a real ArrayBuffer, not a view
    let readCount = 0;
    invokeMock.mockImplementation((cmd: string) => {
      if (cmd === 'pty_spawn') return Promise.resolve(11);
      if (cmd === 'pty_read') {
        readCount += 1;
        if (readCount === 1) return Promise.resolve(ab);
        return Promise.reject('EOF');
      }
      return Promise.resolve();
    });

    const received: string[] = [];
    const pty = spawn('/bin/zsh', [], { cols: 80, rows: 24 });
    pty.onData((d) => received.push(d));
    await vi.waitFor(() => expect(received.join('')).toBe('hi$ '), { timeout: 1000 });
  });

  it('AC2: read decodes ALL Tauri-delivered shapes (ArrayBuffer, Uint8Array, number[]) identically', async () => {
    const text = 'ok';
    const enc = new TextEncoder().encode(text); // [111, 107]
    const shapes: Record<string, ArrayBuffer | Uint8Array | number[]> = {
      arraybuffer: enc.buffer.slice(0),
      uint8array: enc,
      numberarray: Array.from(enc),
    };
    for (const [name, chunk] of Object.entries(shapes)) {
      let readCount = 0;
      invokeMock.mockReset();
      invokeMock.mockImplementation((cmd: string) => {
        if (cmd === 'pty_spawn') return Promise.resolve(1);
        if (cmd === 'pty_read') {
          readCount += 1;
          if (readCount === 1) return Promise.resolve(chunk);
          return Promise.reject('EOF');
        }
        return Promise.resolve();
      });
      const received: string[] = [];
      const pty = spawn('/bin/zsh', [], { cols: 80, rows: 24 });
      pty.onData((d) => received.push(d));
      await vi.waitFor(() => expect(received.join('')).toBe(text), { timeout: 1000 });
      pty.kill();
    }
  });

  it('AC3: a real pty_write error is LOGGED (not silently swallowed)', async () => {
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    invokeMock.mockImplementation((cmd: string) => {
      if (cmd === 'pty_spawn') return Promise.resolve(21);
      if (cmd === 'pty_read') return new Promise(() => {});
      if (cmd === 'pty_write') return Promise.reject('disk on fire');
      return Promise.resolve();
    });
    const pty = spawn('/bin/zsh', [], { cols: 80, rows: 24 });
    await new Promise((r) => setTimeout(r, 0));
    pty.write('ls\n');
    await vi.waitFor(() => expect(errSpy).toHaveBeenCalled(), { timeout: 1000 });
    expect(errSpy.mock.calls[0].join(' ')).toContain('pty write error');
    pty.kill();
  });

  it('AC3: expected teardown errors (EOF, Unavailable pid) do NOT log (no false-alarm spam)', async () => {
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    invokeMock.mockImplementation((cmd: string) => {
      if (cmd === 'pty_spawn') return Promise.resolve(22);
      if (cmd === 'pty_read') return new Promise(() => {});
      if (cmd === 'pty_kill') return Promise.reject('Unavailable pid'); // normal on teardown
      return Promise.resolve();
    });
    const pty = spawn('/bin/zsh', [], { cols: 80, rows: 24 });
    await new Promise((r) => setTimeout(r, 0));
    pty.kill();
    await new Promise((r) => setTimeout(r, 20));
    expect(errSpy).not.toHaveBeenCalled(); // "Unavailable pid" is expected → silent
  });

  it('AC3: pty_spawn failure fires onExit and logs (was an invisible unhandled rejection)', async () => {
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    invokeMock.mockImplementation((cmd: string) => {
      if (cmd === 'pty_spawn') return Promise.reject('spawn failed: bad shell');
      return Promise.resolve();
    });
    const pty = spawn('/bad/shell', [], { cols: 80, rows: 24 });
    const exits: number[] = [];
    pty.onExit(({ exitCode }) => exits.push(exitCode));
    await vi.waitFor(() => expect(errSpy).toHaveBeenCalled(), { timeout: 1000 });
    expect(errSpy.mock.calls[0].join(' ')).toContain('pty spawn error');
    await vi.waitFor(() => expect(exits).toContain(-1), { timeout: 1000 });
  });

  it('AC3: a LATE onExit subscriber still learns of a spawn failure (replay-on-subscribe)', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    invokeMock.mockImplementation((cmd: string) => {
      if (cmd === 'pty_spawn') return Promise.reject('spawn failed');
      return Promise.resolve();
    });
    const pty = spawn('/bad', [], { cols: 80, rows: 24 });
    // Subscribe AFTER the spawn rejection has had time to resolve (the race the
    // Gate-2 MED flagged: fire-then-clear would lose this). Latch+replay fixes it.
    await new Promise((r) => setTimeout(r, 20));
    const exits: number[] = [];
    pty.onExit(({ exitCode }) => exits.push(exitCode));
    await vi.waitFor(() => expect(exits).toContain(-1), { timeout: 1000 });
  });

  it('AC2: write forwards to pty_write with the handle and data', async () => {
    invokeMock.mockImplementation((cmd: string) => {
      if (cmd === 'pty_spawn') return Promise.resolve(3);
      if (cmd === 'pty_read') return new Promise(() => {});
      return Promise.resolve();
    });

    const pty = spawn('/bin/zsh', [], { cols: 80, rows: 24 });
    await new Promise((r) => setTimeout(r, 0));
    invokeMock.mockClear();
    pty.write('ls\n');
    await new Promise((r) => setTimeout(r, 0));

    expect(invokeMock).toHaveBeenCalledWith('pty_write', { pid: 3, data: 'ls\n' });
    pty.kill();
  });

  it('AC6: kill forwards to pty_kill and stops the poll loop', async () => {
    let reads = 0;
    invokeMock.mockImplementation((cmd: string) => {
      if (cmd === 'pty_spawn') return Promise.resolve(9);
      if (cmd === 'pty_read') { reads += 1; return new Promise(() => {}); }
      return Promise.resolve();
    });

    const pty = spawn('/bin/zsh', [], { cols: 80, rows: 24 });
    await new Promise((r) => setTimeout(r, 0));
    invokeMock.mockClear();
    pty.kill();
    await new Promise((r) => setTimeout(r, 0));

    expect(invokeMock).toHaveBeenCalledWith('pty_kill', { pid: 9 });
  });

  it('M1: onData returns a disposable that stops delivering to that listener', async () => {
    const bytes = new TextEncoder().encode('x');
    let readCount = 0;
    invokeMock.mockImplementation((cmd: string) => {
      if (cmd === 'pty_spawn') return Promise.resolve(2);
      if (cmd === 'pty_read') {
        readCount += 1;
        if (readCount <= 2) return Promise.resolve(bytes);
        return Promise.reject('EOF');
      }
      return Promise.resolve();
    });

    const received: string[] = [];
    const pty = spawn('/bin/zsh', [], { cols: 80, rows: 24 });
    const disposable = pty.onData((d) => received.push(d));
    // dispose immediately — listener should get nothing (or stop)
    disposable.dispose();

    await new Promise((r) => setTimeout(r, 50));
    expect(received.length).toBe(0);
  });

  it('AC2: resize forwards cols/rows to pty_resize', async () => {
    invokeMock.mockImplementation((cmd: string) => {
      if (cmd === 'pty_spawn') return Promise.resolve(5);
      if (cmd === 'pty_read') return new Promise(() => {});
      return Promise.resolve();
    });

    const pty = spawn('/bin/zsh', [], { cols: 80, rows: 24 });
    await new Promise((r) => setTimeout(r, 0));
    invokeMock.mockClear();
    pty.resize(120, 40);
    await new Promise((r) => setTimeout(r, 0));

    expect(invokeMock).toHaveBeenCalledWith('pty_resize', { pid: 5, cols: 120, rows: 40 });
    pty.kill();
  });
});
