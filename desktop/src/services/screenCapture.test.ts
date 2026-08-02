/**
 * Tests for captureCurrentScreen (services/tauri.ts).
 *
 * captureCurrentScreen wraps the app-level Rust command
 * `screen_capture_current_display` (returns a PNG path) + plugin-fs `readFile`
 * into a browser `File` the chat attachment pipeline accepts. We mock ONLY the
 * two system boundaries — the `invoke` IPC call and the `readFile` fs call
 * (Boundary-Only mock rule) — everything else is the real service.
 *
 * Covers: AC3 (invoke→readFile→File, type=image/png) and AC5's service half
 * (Rust Err propagates as a rejected promise the caller can toast).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const invokeMock = vi.fn();
const readFileMock = vi.fn();
vi.mock('@tauri-apps/api/core', () => ({
  invoke: (...args: unknown[]) => invokeMock(...args),
}));
vi.mock('@tauri-apps/plugin-fs', () => ({
  readFile: (...args: unknown[]) => readFileMock(...args),
}));

import { captureCurrentScreen } from './tauri';

describe('captureCurrentScreen', () => {
  beforeEach(() => {
    invokeMock.mockReset();
    readFileMock.mockReset();
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('AC3: invokes the Rust command, reads the returned path, returns a PNG File', async () => {
    invokeMock.mockResolvedValue('/Users/x/.swarm-ai/tmp/screenshots/shot-123.png');
    const bytes = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 1, 2, 3, 4]); // PNG magic + payload
    readFileMock.mockResolvedValue(bytes);

    const file = await captureCurrentScreen();

    expect(invokeMock).toHaveBeenCalledWith('screen_capture_current_display');
    expect(readFileMock).toHaveBeenCalledWith('/Users/x/.swarm-ai/tmp/screenshots/shot-123.png');
    expect(file).toBeInstanceOf(File);
    expect(file.type).toBe('image/png');
    expect(file.name).toMatch(/\.png$/);
    expect(file.size).toBe(bytes.length);
  });

  it('AC5(service): a Rust Err rejects the promise so the caller can toast', async () => {
    invokeMock.mockRejectedValue('capture failed (grant Screen Recording to SwarmAI)');
    await expect(captureCurrentScreen()).rejects.toBeTruthy();
    expect(readFileMock).not.toHaveBeenCalled();
  });
});
