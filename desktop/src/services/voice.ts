/**
 * Voice synthesis (TTS) API client.
 *
 * Calls the backend POST /api/voice/synthesize to convert text to MP3 audio.
 *
 * @module services/voice
 */

import { getBackendPort } from './tauri';

/** Available voice information per language */
export interface VoiceInfo {
  voices: Record<string, [string, string]>; // language → [voice_id, engine]
}

/**
 * Synthesize text to MP3 audio via Amazon Polly.
 *
 * @param text - Text to speak (max 3000 chars)
 * @param language - BCP-47 language code (default "en-US")
 * @param voiceId - Optional Polly voice ID override
 * @returns MP3 audio as ArrayBuffer
 * @throws Error if synthesis fails
 */
export async function synthesizeSpeech(
  text: string,
  language?: string,
  voiceId?: string,
): Promise<ArrayBuffer> {
  const port = getBackendPort();
  const response = await fetch(`http://localhost:${port}/api/voice/synthesize`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      text,
      language: language || 'en-US',
      voice_id: voiceId || null,
    }),
  });

  if (!response.ok) {
    const detail = await response.text().catch(() => 'Unknown error');
    throw new Error(`TTS synthesis failed (${response.status}): ${detail}`);
  }

  return response.arrayBuffer();
}

/**
 * Get available TTS voices per language.
 *
 * @returns Voice map: { voices: { "en-US": ["Matthew", "neural"], ... } }
 */
export async function getVoices(): Promise<VoiceInfo> {
  const port = getBackendPort();
  const response = await fetch(`http://localhost:${port}/api/voice/voices`);

  if (!response.ok) {
    throw new Error(`Failed to fetch voices (${response.status})`);
  }

  return response.json();
}
