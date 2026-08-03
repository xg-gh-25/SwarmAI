/**
 * UnsupportedRenderer — Tauri-detection + OS-action wiring (run_847ed9f9).
 *
 * Bug history: the card gated its "Open in Default App" / "Reveal in Finder"
 * buttons and its Copy-Path target on a LOCAL `isTauriContext()` that checked
 * `'__TAURI__' in window`. Tauri 2.x injects `__TAURI_INTERNALS__` (not
 * `__TAURI__`, unless `withGlobalTauri: true` — which this project does NOT set),
 * so in the packaged app the check was ALWAYS false: Open + Reveal were hidden and
 * Copy-Path copied the workspace-RELATIVE `filePath` instead of the absolute
 * `osPath`. Fix: reuse the canonical `isDesktop()` SSOT from services/tauri (which
 * checks `__TAURI_INTERNALS__ || __TAURI__`).
 *
 * Invariants under test (all assert against a Tauri-2.x window, i.e. only
 * `__TAURI_INTERNALS__` present — the exact prod shape the old check missed):
 *  - Open in Default App + Reveal in Finder buttons ARE rendered
 *  - Copy Path copies the ABSOLUTE absolutePath (not the relative filePath)
 *  - Open in Default App invokes the opener with the absolute path
 *
 * MUTATION NOTE: reverting UnsupportedRenderer's detection to
 * `'__TAURI__' in window` (the old bug) makes the button-presence + absolute-copy
 * assertions go RED — this is what proves the test is non-vacuous.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react';
import UnsupportedRenderer from '../UnsupportedRenderer';

const mockOpenInSystemApp = vi.fn();
const mockRevealInFolder = vi.fn();
const mockCopyToClipboard = vi.fn(() => Promise.resolve(true));

vi.mock('../../../../utils/openExternal', () => ({
  openInSystemApp: (...a: unknown[]) => mockOpenInSystemApp(...a),
  revealInFolder: (...a: unknown[]) => mockRevealInFolder(...a),
}));
vi.mock('../../../../utils/clipboard', () => ({
  copyToClipboard: (...a: unknown[]) => mockCopyToClipboard(...a),
}));

const REL_PATH = 'Projects/AIDLC/assets/deck.pptx';
const ABS_PATH = '/Users/gawan/.swarm-ai/SwarmWS/Projects/AIDLC/assets/deck.pptx';

const PROPS = {
  filePath: REL_PATH,
  fileName: 'deck.pptx',
  content: null,
  encoding: 'base64' as const,
  mimeType: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
  fileSize: 20480,
  absolutePath: ABS_PATH,
};

describe('UnsupportedRenderer — Tauri 2.x desktop detection (run_847ed9f9)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Tauri 2.x injects __TAURI_INTERNALS__ ONLY. This is the exact prod shape
    // the old `'__TAURI__' in window` check failed to recognize.
    (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {};
    delete (window as unknown as Record<string, unknown>).__TAURI__;
  });

  afterEach(() => {
    delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__;
    cleanup();
  });

  it('renders Open in Default App + Reveal in Finder in a Tauri 2.x desktop shell', () => {
    render(<UnsupportedRenderer {...PROPS} />);
    expect(screen.getByText('Open in Default App')).toBeTruthy();
    expect(screen.getByText('Reveal in Finder')).toBeTruthy();
    expect(screen.getByText('Copy Path')).toBeTruthy();
  });

  it('Copy Path copies the ABSOLUTE path (not the workspace-relative filePath)', async () => {
    render(<UnsupportedRenderer {...PROPS} />);
    fireEvent.click(screen.getByText('Copy Path'));
    await waitFor(() => expect(mockCopyToClipboard).toHaveBeenCalledTimes(1));
    expect(mockCopyToClipboard).toHaveBeenCalledWith(ABS_PATH);
    expect(mockCopyToClipboard).not.toHaveBeenCalledWith(REL_PATH);
  });

  it('Open in Default App opens the absolute path', async () => {
    render(<UnsupportedRenderer {...PROPS} />);
    fireEvent.click(screen.getByText('Open in Default App'));
    await waitFor(() => expect(mockOpenInSystemApp).toHaveBeenCalledWith(ABS_PATH));
  });
});
