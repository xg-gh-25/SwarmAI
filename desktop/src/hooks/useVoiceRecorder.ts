/**
 * Voice recording hook using MediaRecorder API.
 *
 * State machine: idle → recording → processing → idle
 *                                    ↓ error
 *                                   idle
 *
 * Returns audio blob for backend transcription via Amazon Transcribe.
 */

import { useState, useRef, useCallback, useEffect } from 'react';
import { transcribeAudio } from '../services/chat';

export type VoiceState = 'idle' | 'recording' | 'processing';

interface UseVoiceRecorderOptions {
  /** Called when transcription succeeds — receives transcript text */
  onTranscript: (text: string) => void;
  /** Called on any error (permission denied, transcription failure, etc.) */
  onError?: (error: string) => void;
  /** Minimum recording duration in ms (default: 500) */
  minDurationMs?: number;
}

interface UseVoiceRecorderReturn {
  /** Current state of the voice recorder */
  voiceState: VoiceState;
  /** Toggle recording: idle→recording or recording→stop+process */
  toggleRecording: () => void;
  /** Start recording programmatically (for voice conversation mode) */
  startRecording: () => void;
  /** Stop recording programmatically (for voice conversation mode) */
  stopRecording: () => void;
  /** Whether the browser supports MediaRecorder */
  isSupported: boolean;
}

export function useVoiceRecorder({
  onTranscript,
  onError,
  minDurationMs = 500,
}: UseVoiceRecorderOptions): UseVoiceRecorderReturn {
  const [voiceState, setVoiceState] = useState<VoiceState>('idle');
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const startTimeRef = useRef<number>(0);
  const streamRef = useRef<MediaStream | null>(null);
  // Track whether the hook is still mounted to prevent stale setState calls
  const mountedRef = useRef(true);

  const isSupported =
    typeof navigator !== 'undefined' &&
    typeof navigator.mediaDevices !== 'undefined' &&
    typeof MediaRecorder !== 'undefined';

  // Cleanup on unmount: release mic + kill recording
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
        mediaRecorderRef.current.stop();
      }
      if (streamRef.current) {
        streamRef.current.getTracks().forEach((t) => t.stop());
        streamRef.current = null;
      }
      mediaRecorderRef.current = null;
    };
  }, []);

  const startRecording = useCallback(async () => {
    if (!isSupported) {
      onError?.('Voice recording is not supported in this browser');
      return;
    }

    try {
      // Don't constrain sampleRate — WKWebView (Tauri/Safari engine) can't
      // reconfigure hardware capture away from 44.1/48kHz and throws a
      // "MediaStreamTrack ended due to a capture failure". Backend ffmpeg
      // resamples to 16kHz anyway, so native rate is fine.
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
        },
      });

      // Component may have unmounted while waiting for permission dialog
      if (!mountedRef.current) {
        stream.getTracks().forEach((t) => t.stop());
        return;
      }

      streamRef.current = stream;

      // Detect if the capture track dies (hardware failure, permission revoke,
      // device disconnect). Without this, a dead track produces a corrupt blob
      // that gets sent to the backend → 400.
      const track = stream.getAudioTracks()[0];
      if (track) {
        track.onended = () => {
          if (mountedRef.current && mediaRecorderRef.current?.state === 'recording') {
            onError?.('Microphone disconnected — recording stopped');
            // Force stop without sending
            try { mediaRecorderRef.current.stop(); } catch { /* already stopped */ }
            chunksRef.current = [];
            if (streamRef.current) {
              streamRef.current.getTracks().forEach((t) => t.stop());
              streamRef.current = null;
            }
            mediaRecorderRef.current = null;
            setVoiceState('idle');
          }
        };
      }

      // Prefer formats that backend ffmpeg can handle; fall back to default
      let mimeType = '';
      if (MediaRecorder.isTypeSupported('audio/webm;codecs=opus')) {
        mimeType = 'audio/webm;codecs=opus';
      } else if (MediaRecorder.isTypeSupported('audio/mp4')) {
        mimeType = 'audio/mp4';
      }
      // Empty string → platform default (Safari often uses mp4/aac)

      const recorder = mimeType
        ? new MediaRecorder(stream, { mimeType })
        : new MediaRecorder(stream);

      chunksRef.current = [];
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };

      // Handle recorder-level errors (e.g. codec failure, resource exhaustion)
      recorder.onerror = () => {
        if (!mountedRef.current) return;
        onError?.('Recording failed — please try again');
        chunksRef.current = [];
        if (streamRef.current) {
          streamRef.current.getTracks().forEach((t) => t.stop());
          streamRef.current = null;
        }
        mediaRecorderRef.current = null;
        setVoiceState('idle');
      };

      recorder.start(100); // collect data every 100ms
      startTimeRef.current = Date.now();
      mediaRecorderRef.current = recorder;
      setVoiceState('recording');
    } catch (err: unknown) {
      if (!mountedRef.current) return;
      const message =
        err instanceof DOMException && err.name === 'NotAllowedError'
          ? 'Microphone permission denied. Please allow access in System Settings.'
          : `Failed to start recording: ${err instanceof Error ? err.message : String(err)}`;
      onError?.(message);
      setVoiceState('idle');
    }
  }, [isSupported, onError]);

  const stopRecording = useCallback(async () => {
    const recorder = mediaRecorderRef.current;
    if (!recorder || recorder.state === 'inactive') {
      if (mountedRef.current) setVoiceState('idle');
      return;
    }

    // Wait for final data with timeout guard
    try {
      await Promise.race([
        new Promise<void>((resolve) => {
          recorder.onstop = () => resolve();
          recorder.stop();
        }),
        new Promise<void>((_, reject) =>
          setTimeout(() => reject(new Error('Stop timeout')), 5000),
        ),
      ]);
    } catch {
      // If stop times out, force-kill the recorder
      try { recorder.stop(); } catch { /* already stopped */ }
    }

    // Always release mic
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
    mediaRecorderRef.current = null;

    if (!mountedRef.current) return;

    const duration = Date.now() - startTimeRef.current;
    if (duration < minDurationMs) {
      onError?.('Recording too short — please hold longer');
      chunksRef.current = [];
      setVoiceState('idle');
      return;
    }

    const blob = new Blob(chunksRef.current, { type: recorder.mimeType || 'audio/webm' });
    chunksRef.current = [];

    if (blob.size === 0) {
      onError?.('No audio captured');
      setVoiceState('idle');
      return;
    }

    // Send to backend for transcription
    setVoiceState('processing');
    try {
      const result = await transcribeAudio(blob);
      if (!mountedRef.current) return;
      if (result.transcript) {
        onTranscript(result.transcript);
      } else {
        onError?.('No speech detected — try again');
      }
    } catch (err: unknown) {
      if (!mountedRef.current) return;
      onError?.(
        `Transcription failed: ${err instanceof Error ? err.message : String(err)}`,
      );
    } finally {
      if (mountedRef.current) setVoiceState('idle');
    }
  }, [minDurationMs, onTranscript, onError]);

  const toggleRecording = useCallback(() => {
    if (voiceState === 'idle') {
      startRecording();
    } else if (voiceState === 'recording') {
      stopRecording();
    }
    // If processing, do nothing (wait for result)
  }, [voiceState, startRecording, stopRecording]);

  return { voiceState, toggleRecording, startRecording, stopRecording, isSupported };
}
