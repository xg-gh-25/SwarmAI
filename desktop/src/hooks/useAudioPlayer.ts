/**
 * Audio playback hook using Web Audio API.
 *
 * Provides queued MP3 playback with GainNode routing for click-free
 * interruption. Supports enqueue (sequential play), stop (instant silence
 * via gain fade), and drain (stop after current).
 *
 * Based on VoiceBox lessons: GainNode routing for seek-pop prevention,
 * precise scheduling for gapless playback.
 *
 * @module useAudioPlayer
 */

import { useCallback, useEffect, useRef, useState } from 'react';

export interface UseAudioPlayerReturn {
  /** Play MP3 audio bytes. Resolves when playback completes. */
  play: (audioData: ArrayBuffer) => Promise<void>;
  /** Stop current playback immediately (gain fade-out) */
  stop: () => void;
  /** Queue audio to play after current finishes */
  enqueue: (audioData: ArrayBuffer) => void;
  /** Whether audio is currently playing */
  isPlaying: boolean;
  /** Drain the queue (stop after current, don't play more) */
  drain: () => void;
  /** Clear everything and reset state */
  reset: () => void;
}

export function useAudioPlayer(): UseAudioPlayerReturn {
  const [isPlaying, setIsPlaying] = useState(false);

  // Refs for Web Audio API objects
  const ctxRef = useRef<AudioContext | null>(null);
  const gainRef = useRef<GainNode | null>(null);
  const sourceRef = useRef<AudioBufferSourceNode | null>(null);
  const queueRef = useRef<ArrayBuffer[]>([]);
  const drainingRef = useRef(false);
  const playingRef = useRef(false);

  // Ensure AudioContext exists (lazy init — needs user gesture)
  const ensureContext = useCallback((): AudioContext => {
    if (!ctxRef.current || ctxRef.current.state === 'closed') {
      ctxRef.current = new AudioContext();
      // Create GainNode for click-free control
      const gain = ctxRef.current.createGain();
      gain.connect(ctxRef.current.destination);
      gainRef.current = gain;
    }

    // Resume if suspended (autoplay policy)
    if (ctxRef.current.state === 'suspended') {
      ctxRef.current.resume();
    }

    return ctxRef.current;
  }, []);

  // Play a single audio buffer through the gain node
  const playBuffer = useCallback(async (audioData: ArrayBuffer): Promise<void> => {
    const ctx = ensureContext();
    const gain = gainRef.current!;

    // Decode MP3 → AudioBuffer
    const audioBuffer = await ctx.decodeAudioData(audioData.slice(0));

    return new Promise<void>((resolve) => {
      const source = ctx.createBufferSource();
      source.buffer = audioBuffer;
      source.connect(gain);
      sourceRef.current = source;

      // Ensure gain is at full volume
      gain.gain.setValueAtTime(1, ctx.currentTime);

      // Crossfade: short linear ramp-up to prevent pops at chunk boundaries
      gain.gain.setValueAtTime(0, ctx.currentTime);
      gain.gain.linearRampToValueAtTime(1, ctx.currentTime + 0.005);

      source.onended = () => {
        sourceRef.current = null;
        resolve();
      };

      source.start(0);
    });
  }, [ensureContext]);

  // Process queue sequentially
  const processQueue = useCallback(async () => {
    if (playingRef.current) return; // Already processing
    playingRef.current = true;
    setIsPlaying(true);

    while (queueRef.current.length > 0 && !drainingRef.current) {
      const next = queueRef.current.shift()!;
      try {
        await playBuffer(next);
      } catch (err) {
        // Skip failed decode — don't block queue
        console.warn('Audio playback failed:', err);
      }
    }

    playingRef.current = false;
    drainingRef.current = false;
    setIsPlaying(false);
  }, [playBuffer]);

  // Public API
  const play = useCallback(async (audioData: ArrayBuffer): Promise<void> => {
    drainingRef.current = false;
    queueRef.current = [audioData];
    await processQueue();
  }, [processQueue]);

  const enqueue = useCallback((audioData: ArrayBuffer) => {
    queueRef.current.push(audioData);
    if (!playingRef.current) {
      processQueue();
    }
  }, [processQueue]);

  const stop = useCallback(() => {
    // Click-free interruption via GainNode fade-out (5ms)
    const ctx = ctxRef.current;
    const gain = gainRef.current;
    const source = sourceRef.current;

    if (ctx && gain) {
      gain.gain.setTargetAtTime(0, ctx.currentTime, 0.005);
    }

    // Clear queue
    queueRef.current = [];
    drainingRef.current = true;

    // Stop source after fade-out
    if (source) {
      setTimeout(() => {
        try {
          source.stop();
        } catch {
          // Already stopped
        }
        sourceRef.current = null;
      }, 10);
    }

    playingRef.current = false;
    setIsPlaying(false);
  }, []);

  const drain = useCallback(() => {
    // Stop after current audio finishes — don't play more from queue
    drainingRef.current = true;
    queueRef.current = [];
  }, []);

  const reset = useCallback(() => {
    stop();
    if (ctxRef.current && ctxRef.current.state !== 'closed') {
      ctxRef.current.close();
    }
    ctxRef.current = null;
    gainRef.current = null;
    sourceRef.current = null;
  }, [stop]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      queueRef.current = [];
      drainingRef.current = true;
      if (sourceRef.current) {
        try { sourceRef.current.stop(); } catch { /* noop */ }
      }
      if (ctxRef.current && ctxRef.current.state !== 'closed') {
        ctxRef.current.close();
      }
    };
  }, []);

  return { play, stop, enqueue, isPlaying, drain, reset };
}
