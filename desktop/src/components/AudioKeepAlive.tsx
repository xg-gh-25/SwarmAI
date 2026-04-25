/**
 * AudioKeepAlive — Prevents WKWebView from tearing down CoreAudio session.
 *
 * Mount this at the app root. It plays a silent WAV file in a loop to keep
 * macOS's CoreAudio session "warm" for the lifetime of the app.
 *
 * Without this, WKWebView (Tauri's macOS WebView engine) silently tears down
 * the audio session after backgrounding or idle. AudioContext stays alive in JS
 * but produces no sound. play() resolves, events fire, but speakers are silent.
 * Only a full app restart recovers.
 *
 * Based on VoiceBox PR #486 pattern (jamiepine/voicebox).
 *
 * @module AudioKeepAlive
 */

import { useEffect, useRef } from 'react';

/**
 * Generate a data URL for a 1-second silent WAV file.
 *
 * Uses real zero-value PCM samples (not muted audio) because browsers
 * and WebKit can optimize muted/zero-volume media away.
 */
function buildSilentWavDataUrl(durationSec: number = 1, sampleRate: number = 8000): string {
  const numSamples = sampleRate * durationSec;
  const numChannels = 1;
  const bitsPerSample = 16;
  const bytesPerSample = bitsPerSample / 8;
  const dataSize = numSamples * numChannels * bytesPerSample;
  const headerSize = 44;
  const fileSize = headerSize + dataSize;

  const buffer = new ArrayBuffer(fileSize);
  const view = new DataView(buffer);

  // WAV header
  const writeString = (offset: number, str: string) => {
    for (let i = 0; i < str.length; i++) {
      view.setUint8(offset + i, str.charCodeAt(i));
    }
  };

  writeString(0, 'RIFF');
  view.setUint32(4, fileSize - 8, true);
  writeString(8, 'WAVE');
  writeString(12, 'fmt ');
  view.setUint32(16, 16, true); // chunk size
  view.setUint16(20, 1, true); // PCM format
  view.setUint16(22, numChannels, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * numChannels * bytesPerSample, true);
  view.setUint16(32, numChannels * bytesPerSample, true);
  view.setUint16(34, bitsPerSample, true);
  writeString(36, 'data');
  view.setUint32(40, dataSize, true);

  // PCM data — all zeros = silence (but real samples, not muted)

  // Convert to base64 data URL
  const bytes = new Uint8Array(buffer);
  let binary = '';
  for (let i = 0; i < bytes.length; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  return `data:audio/wav;base64,${btoa(binary)}`;
}

/**
 * AudioKeepAlive component — renders nothing, just keeps audio alive.
 *
 * Mount once at app root (App.tsx or similar). Never unmount.
 */
export function AudioKeepAlive() {
  const audioRef = useRef<HTMLAudioElement | null>(null);

  useEffect(() => {
    const wavUrl = buildSilentWavDataUrl(1, 8000);
    const el = new Audio(wavUrl);
    el.loop = true;
    el.volume = 0.001; // Near-zero but non-zero — prevents browser optimization. Inaudible on all hardware including studio monitors.
    audioRef.current = el;

    // Play — may fail silently if user hasn't interacted yet (autoplay policy).
    // That's fine — first user gesture (mic toggle) will resume AudioContext anyway.
    el.play().catch(() => {
      // Autoplay policy blocked — will retry on first user interaction
    });

    return () => {
      if (audioRef.current) {
        audioRef.current.pause();
        audioRef.current.src = '';
        audioRef.current = null;
      }
    };
  }, []);

  return null;
}
