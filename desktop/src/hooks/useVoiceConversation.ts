/**
 * Voice Conversation Mode orchestrator hook.
 *
 * Manages the bidirectional conversation loop:
 *   listening → processing → thinking → speaking → listening → ...
 *
 * Reads streaming output from existing useChatStreamingLifecycle,
 * feeds sentences through sentenceSplitter → TTS service → useAudioPlayer.
 *
 * State machine:
 *   off → listening → processing → thinking → speaking → listening (loop)
 *   any → off (toggle off / tab blur / error)
 *   speaking → interrupted → listening (user speaks during playback)
 *
 * @module useVoiceConversation
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useAudioPlayer } from './useAudioPlayer';
import { useVoiceRecorder } from './useVoiceRecorder';
import { synthesizeSpeech } from '../services/voice';
import { extractSentences, flushRemaining } from '../utils/sentenceSplitter';

/** Voice conversation states */
export type VoiceConversationState =
  | 'off'          // Normal text mode
  | 'listening'    // Mic open, waiting for user speech
  | 'processing'   // STT in progress → auto-send
  | 'thinking'     // Waiting for Claude's first text_delta
  | 'speaking'     // TTS playing audio chunks
  | 'interrupted'; // User spoke during playback → transition to listening

export interface UseVoiceConversationOptions {
  /** Session ID for the current chat tab */
  sessionId: string | null;
  /**
   * Callback to send a voice transcript as a chat message.
   * Must accept the text directly (not read from React state) to avoid
   * race conditions with setTimeout-based state propagation.
   */
  onSendMessage: (text: string) => void;
  /** Whether streaming is currently in progress */
  isStreaming: boolean;
  /** Latest accumulated text content from SSE stream */
  latestTextContent: string;
  /** Whether the response stream is complete */
  isResponseComplete: boolean;
}

export interface UseVoiceConversationReturn {
  /** Current conversation state */
  state: VoiceConversationState;
  /** Toggle voice conversation mode on/off */
  toggle: () => void;
  /** Interrupt TTS playback and return to listening (speaking → listening) */
  interrupt: () => void;
  /** Whether voice conversation is supported (mic + audio) */
  isSupported: boolean;
}

/**
 * Detect language from text — simple heuristic.
 * If >30% CJK characters → "zh-CN", otherwise "en-US".
 */
function detectLanguage(text: string): string {
  const cjkRegex = /[\u4e00-\u9fff\u3400-\u4dbf\u3000-\u303f\uff00-\uffef]/g;
  const cjkCount = (text.match(cjkRegex) || []).length;
  const totalChars = text.replace(/\s/g, '').length;
  if (totalChars > 0 && cjkCount / totalChars > 0.3) {
    return 'zh-CN';
  }
  return 'en-US';
}

export function useVoiceConversation({
  sessionId,
  onSendMessage,
  isStreaming,
  latestTextContent,
  isResponseComplete,
}: UseVoiceConversationOptions): UseVoiceConversationReturn {
  // sessionId and isStreaming reserved for future use (silence detection, per-tab isolation)
  void sessionId;
  void isStreaming;

  const [state, setState] = useState<VoiceConversationState>('off');
  const stateRef = useRef<VoiceConversationState>('off');
  const sentenceBufferRef = useRef<string>('');
  const lastProcessedLenRef = useRef<number>(0);
  const mountedRef = useRef(true);

  // Keep stateRef in sync
  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  // Audio player for TTS output
  const audioPlayer = useAudioPlayer();

  // Transcript handler — auto-send message
  const handleTranscript = useCallback(
    (text: string) => {
      if (!mountedRef.current) return;
      if (stateRef.current === 'off') return;

      setState('thinking');
      onSendMessage(text);
    },
    [onSendMessage],
  );

  // Error handler
  const handleVoiceError = useCallback(
    (error: string) => {
      if (!mountedRef.current) return;
      console.warn('Voice conversation error:', error);
      // Don't exit voice mode on minor errors — just go back to listening
      if (stateRef.current !== 'off') {
        setState('listening');
      }
    },
    [],
  );

  // Voice recorder — VAD enabled for hands-free auto-stop on silence
  const { voiceState, startRecording, stopRecording, isSupported } =
    useVoiceRecorder({
      onTranscript: handleTranscript,
      onError: handleVoiceError,
      enableVAD: true,
      silenceThresholdMs: 1500,
      silenceLevel: 0.01,
    });

  // Sync recorder state → conversation state
  useEffect(() => {
    if (stateRef.current === 'off') return;

    if (voiceState === 'recording' && stateRef.current === 'listening') {
      // Already in listening — recorder is active, good
    } else if (voiceState === 'processing') {
      setState('processing');
    }
  }, [voiceState]);

  // ─── Sentence streaming → TTS ─────────────────────────────────────

  // TTS synthesis queue — ensures sentences play in order regardless of
  // Polly response times. Each batch of sentences is chained sequentially.
  const ttsQueueRef = useRef<Promise<void>>(Promise.resolve());

  // Process new text deltas into sentences for TTS
  useEffect(() => {
    if (stateRef.current !== 'thinking' && stateRef.current !== 'speaking') {
      return;
    }

    if (!latestTextContent || latestTextContent.length <= lastProcessedLenRef.current) {
      return;
    }

    // Get new text since last processing
    const newText = latestTextContent.slice(lastProcessedLenRef.current);
    lastProcessedLenRef.current = latestTextContent.length;

    // Accumulate in sentence buffer
    sentenceBufferRef.current += newText;

    // Extract complete sentences
    const { sentences, remaining } = extractSentences(sentenceBufferRef.current);
    sentenceBufferRef.current = remaining;

    // Queue sentences for TTS — sequential chain preserves order
    if (sentences.length > 0) {
      if (stateRef.current === 'thinking') {
        setState('speaking');
      }

      // Chain each sentence sequentially onto the TTS queue
      for (const sentence of sentences) {
        ttsQueueRef.current = ttsQueueRef.current.then(async () => {
          if (!mountedRef.current || stateRef.current === 'off') return;
          try {
            // Per-sentence language detection (supports mixed en/zh responses)
            const lang = detectLanguage(sentence);
            const audio = await synthesizeSpeech(sentence, lang);
            if (mountedRef.current && stateRef.current === 'speaking') {
              audioPlayer.enqueue(audio);
            }
          } catch (err) {
            console.warn('TTS synthesis failed for sentence:', err);
            // Skip failed sentence, continue with next
          }
        });
      }
    }
  }, [latestTextContent, audioPlayer]);

  // ─── Interrupt: stop TTS + return to listening ─────────────────────

  const interrupt = useCallback(() => {
    if (stateRef.current !== 'speaking') return;

    // Stop all audio playback
    audioPlayer.stop();
    // Reset TTS queue to prevent pending sentences from playing
    ttsQueueRef.current = Promise.resolve();
    // Clear sentence buffer
    sentenceBufferRef.current = '';
    lastProcessedLenRef.current = 0;

    // Transition through interrupted → listening (barge-in flow)
    setState('interrupted');
    // Brief transient state for UI feedback, then re-open mic
    setTimeout(() => {
      if (mountedRef.current && stateRef.current === 'interrupted') {
        setState('listening');
        startRecording();
      }
    }, 150);
  }, [audioPlayer, startRecording]);

  // ─── Stream complete → flush remaining + re-open mic ──────────────

  useEffect(() => {
    if (!isResponseComplete) return;
    if (stateRef.current !== 'speaking' && stateRef.current !== 'thinking') return;

    // Guard: don't flush if no content has been streamed yet (race with
    // isResponseComplete starting as true before streaming begins)
    if (!latestTextContent || latestTextContent.length === 0) return;

    // Flush remaining sentence buffer via the sequential TTS queue
    const remaining = flushRemaining(sentenceBufferRef.current);
    sentenceBufferRef.current = '';
    lastProcessedLenRef.current = 0;

    if (remaining) {
      ttsQueueRef.current = ttsQueueRef.current.then(async () => {
        if (!mountedRef.current || stateRef.current === 'off') return;
        try {
          const lang = detectLanguage(remaining);
          const audio = await synthesizeSpeech(remaining, lang);
          if (mountedRef.current && stateRef.current === 'speaking') {
            audioPlayer.enqueue(audio);
          }
        } catch (err) {
          console.warn('TTS flush failed:', err);
        }
      });
    }
  }, [isResponseComplete, audioPlayer, latestTextContent]);

  // ─── Audio playback complete → re-open mic ────────────────────────

  useEffect(() => {
    // When audio player finishes all queued audio and response is complete
    if (
      !audioPlayer.isPlaying &&
      isResponseComplete &&
      stateRef.current === 'speaking'
    ) {
      // Transition back to listening for next turn
      setState('listening');
      sentenceBufferRef.current = '';
      lastProcessedLenRef.current = 0;
      // Re-open mic
      startRecording();
    }
  }, [audioPlayer.isPlaying, isResponseComplete, startRecording]);

  // ─── Tab visibility handling ──────────────────────────────────────

  useEffect(() => {
    const handleVisibilityChange = () => {
      if (document.hidden && stateRef.current !== 'off') {
        // Exit voice mode when tab loses focus
        setState('off');
        audioPlayer.stop();
        stopRecording();
        sentenceBufferRef.current = '';
        lastProcessedLenRef.current = 0;
        ttsQueueRef.current = Promise.resolve();
      }
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => {
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, [audioPlayer, stopRecording]);

  // ─── Cleanup on unmount ───────────────────────────────────────────

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      audioPlayer.reset();
      sentenceBufferRef.current = '';
      lastProcessedLenRef.current = 0;
      ttsQueueRef.current = Promise.resolve();
    };
  }, [audioPlayer]);

  // ─── Toggle voice mode ────────────────────────────────────────────

  const toggle = useCallback(() => {
    if (stateRef.current === 'off') {
      // Turn on → start listening
      setState('listening');
      sentenceBufferRef.current = '';
      lastProcessedLenRef.current = 0;
      ttsQueueRef.current = Promise.resolve();
      startRecording();
    } else {
      // Turn off → clean up
      setState('off');
      audioPlayer.stop();
      stopRecording();
      sentenceBufferRef.current = '';
      lastProcessedLenRef.current = 0;
      ttsQueueRef.current = Promise.resolve();
    }
  }, [startRecording, stopRecording, audioPlayer]);

  return {
    state,
    toggle,
    interrupt,
    isSupported,
  };
}
