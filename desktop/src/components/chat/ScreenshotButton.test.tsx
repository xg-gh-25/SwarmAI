/**
 * Tests for ScreenshotButton (AC4 + AC5).
 *
 * The button captures the display the cursor is on and hands the resulting File
 * to the chat attachment pipeline. The capture fn is injectable so we exercise
 * the REAL component wiring without mocking Tauri modules (Boundary-Only rule —
 * the injected fn IS the boundary here).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { ScreenshotButton } from './ScreenshotButton';

describe('ScreenshotButton', () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(() => vi.restoreAllMocks());

  it('AC4: on click, captures and passes the File to onCaptured', async () => {
    const file = new File([new Uint8Array([1, 2, 3])], 'shot-1.png', { type: 'image/png' });
    const capture = vi.fn().mockResolvedValue(file);
    const onCaptured = vi.fn();
    const onError = vi.fn();

    render(<ScreenshotButton onCaptured={onCaptured} onError={onError} capture={capture} />);
    fireEvent.click(screen.getByRole('button'));

    await waitFor(() => expect(onCaptured).toHaveBeenCalledWith([file]));
    expect(capture).toHaveBeenCalledOnce();
    expect(onError).not.toHaveBeenCalled();
  });

  it('AC5: on capture failure, calls onError (not onCaptured) — no crash', async () => {
    const capture = vi.fn().mockRejectedValue('grant Screen Recording to SwarmAI');
    const onCaptured = vi.fn();
    const onError = vi.fn();

    render(<ScreenshotButton onCaptured={onCaptured} onError={onError} capture={capture} />);
    fireEvent.click(screen.getByRole('button'));

    await waitFor(() => expect(onError).toHaveBeenCalledOnce());
    expect(onCaptured).not.toHaveBeenCalled();
  });

  it('is disabled when disabled prop is set (does not capture)', async () => {
    const capture = vi.fn();
    render(<ScreenshotButton onCaptured={vi.fn()} onError={vi.fn()} capture={capture} disabled />);
    const btn = screen.getByRole('button');
    expect(btn).toBeDisabled();
    fireEvent.click(btn);
    expect(capture).not.toHaveBeenCalled();
  });

  it('does not fire a second capture while one is in flight', async () => {
    let resolve!: (f: File) => void;
    const capture = vi.fn().mockReturnValue(new Promise<File>((r) => { resolve = r; }));
    render(<ScreenshotButton onCaptured={vi.fn()} onError={vi.fn()} capture={capture} />);
    const btn = screen.getByRole('button');
    fireEvent.click(btn);
    fireEvent.click(btn); // second click while pending
    await waitFor(() => expect(capture).toHaveBeenCalledOnce());
    resolve(new File([new Uint8Array([1])], 's.png', { type: 'image/png' }));
  });
});
