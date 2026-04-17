/**
 * Voice recording hook using MediaRecorder API.
 *
 * State machine: idle → recording → processing → idle
 *                                    ↓ error
 *                                   idle
 *
 * Returns PCM-compatible audio blob for backend transcription.
 */

import { useState, useRef, useCallback } from 'react';
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

  const isSupported =
    typeof navigator !== 'undefined' &&
    typeof navigator.mediaDevices !== 'undefined' &&
    typeof MediaRecorder !== 'undefined';

  const startRecording = useCallback(async () => {
    if (!isSupported) {
      onError?.('Voice recording is not supported in this browser');
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          sampleRate: 16000,
          echoCancellation: true,
          noiseSuppression: true,
        },
      });
      streamRef.current = stream;

      // Prefer formats Transcribe can handle; fall back to default
      const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? 'audio/webm;codecs=opus'
        : MediaRecorder.isTypeSupported('audio/mp4')
          ? 'audio/mp4'
          : '';

      const recorder = mimeType
        ? new MediaRecorder(stream, { mimeType })
        : new MediaRecorder(stream);

      chunksRef.current = [];
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };

      recorder.start(100); // collect data every 100ms
      startTimeRef.current = Date.now();
      mediaRecorderRef.current = recorder;
      setVoiceState('recording');
    } catch (err: unknown) {
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
      setVoiceState('idle');
      return;
    }

    // Wait for final data
    await new Promise<void>((resolve) => {
      recorder.onstop = () => resolve();
      recorder.stop();
    });

    // Release mic
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }

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
      if (result.transcript) {
        onTranscript(result.transcript);
      } else {
        onError?.('No speech detected — try again');
      }
    } catch (err: unknown) {
      onError?.(
        `Transcription failed: ${err instanceof Error ? err.message : String(err)}`,
      );
    } finally {
      setVoiceState('idle');
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

  return { voiceState, toggleRecording, isSupported };
}
