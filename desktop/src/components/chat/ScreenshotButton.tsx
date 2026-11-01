import { useState, useCallback, useEffect, useRef } from 'react';
import clsx from 'clsx';
import { useTranslation } from 'react-i18next';
import { captureCurrentScreen } from '../../services/tauri';

interface ScreenshotButtonProps {
  /** Receives the captured screenshot as a one-element File array (chat attachment pipeline). */
  onCaptured: (files: File[]) => void;
  /** Called with a human-readable message when capture fails (e.g. permission not granted). */
  onError: (message: string) => void;
  disabled?: boolean;
  canAddMore?: boolean;
  className?: string;
  /**
   * Capture function — defaults to the real Tauri command. Injectable so the
   * component's orchestration (click → capture → onCaptured/onError) is unit
   * tested without mocking Tauri modules.
   */
  capture?: () => Promise<File>;
}

/**
 * One-tap screenshot button for the chat input.
 *
 * Captures the display the mouse cursor is on (via the signed Tauri main
 * process — the only process holding macOS Screen-Recording permission) and
 * hands the PNG to the attachment pipeline, where it flows through the same
 * validation as a pasted image. On failure it calls `onError` (fail-loud) —
 * never silently drops or crashes.
 */
export function ScreenshotButton({
  onCaptured,
  onError,
  disabled = false,
  canAddMore = true,
  className,
  capture = captureCurrentScreen,
}: ScreenshotButtonProps) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  // Guard against setState-after-unmount if the component unmounts (e.g. tab
  // switch) while a capture is still in flight.
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const handleClick = useCallback(async () => {
    if (disabled || !canAddMore || busy) return;
    setBusy(true);
    try {
      const file = await capture();
      onCaptured([file]);
    } catch (e) {
      onError(
        typeof e === 'string'
          ? e
          : e instanceof Error
            ? e.message
            : t('ui.ScreenshotButton.failed')
      );
    } finally {
      if (mountedRef.current) setBusy(false);
    }
  }, [disabled, canAddMore, busy, capture, onCaptured, onError, t]);

  const isDisabled = disabled || !canAddMore || busy;

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={isDisabled}
      className={clsx(
        'icon-btn transition-colors',
        isDisabled
          ? 'text-[var(--color-text-muted)]/50 cursor-not-allowed'
          : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)]',
        className
      )}
      title={
        !canAddMore
          ? t('ui.ScreenshotButton.maxReached')
          : busy
            ? t('ui.ScreenshotButton.capturing')
            : t('ui.ScreenshotButton.capture')
      }
    >
      <span className="material-symbols-outlined icon-sm">screenshot_monitor</span>
    </button>
  );
}
