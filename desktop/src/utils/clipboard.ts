/**
 * Clipboard utility for Tauri webview.
 *
 * `navigator.clipboard.writeText()` silently fails in Tauri's webview because
 * the webview context doesn't have clipboard permissions by default.
 *
 * Strategy (in order):
 *   1. Tauri invoke `copy_to_clipboard` — uses OS-native tools (pbcopy/xclip/PowerShell)
 *   2. navigator.clipboard.writeText (works in standard browsers, fails in Tauri)
 *   3. document.execCommand('copy') via a hidden textarea (legacy fallback)
 */

import { invoke } from '@tauri-apps/api/core';

/**
 * Copy text to the system clipboard.
 * Returns true on success, false on failure.
 */
export async function copyToClipboard(text: string): Promise<boolean> {
  // Attempt 1: Tauri native command (pbcopy / xclip / PowerShell)
  try {
    await invoke('copy_to_clipboard', { text });
    return true;
  } catch {
    // Not in Tauri context or command failed — fall through
  }

  // Attempt 2: Modern Clipboard API (works in regular browsers)
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // Clipboard API denied — fall through to legacy method
    }
  }

  // Attempt 3: Legacy execCommand fallback
  try {
    const textarea = document.createElement('textarea');
    textarea.value = text;
    // Place off-screen to avoid visual flicker
    textarea.style.position = 'fixed';
    textarea.style.left = '-9999px';
    textarea.style.top = '-9999px';
    textarea.style.opacity = '0';
    document.body.appendChild(textarea);
    textarea.focus();
    textarea.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(textarea);
    return ok;
  } catch {
    return false;
  }
}
