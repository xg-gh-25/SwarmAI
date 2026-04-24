/**
 * Streaming sentence splitter for TTS.
 *
 * Incrementally splits streaming text into complete sentences ready for
 * text-to-speech. Handles English/CJK punctuation, abbreviations, code
 * blocks, URLs, and markdown formatting.
 *
 * @module sentenceSplitter
 */

// VoiceBox's 23-entry abbreviation list — periods after these are NOT sentence breaks
const ABBREVIATIONS = new Set([
  'mr', 'mrs', 'ms', 'dr', 'prof', 'sr', 'jr', 'st', 'ave', 'blvd',
  'dept', 'est', 'fig', 'inc', 'ltd', 'no', 'vs', 'vol', 'jan', 'feb',
  'mar', 'apr', 'aug',
]);

// Sentence-ending punctuation
const SENTENCE_ENDERS = /[.!?。！？]/;
const CJK_ENDERS = /[。！？]/;

// (Code block detection + URL detection handled inline by replaceCodeBlocks and isInsideUrl)

// Markdown patterns to strip before TTS
const MARKDOWN_PATTERNS: [RegExp, string][] = [
  [/^#{1,6}\s+/gm, ''],           // Headers: ## Heading → Heading
  [/\*\*(.+?)\*\*/g, '$1'],       // Bold: **text** → text
  [/\*(.+?)\*/g, '$1'],           // Italic: *text* → text
  [/__(.+?)__/g, '$1'],           // Bold: __text__ → text
  [/_(.+?)_/g, '$1'],             // Italic: _text_ → text
  [/~~(.+?)~~/g, '$1'],           // Strikethrough: ~~text~~ → text
  [/\[([^\]]+)\]\([^)]+\)/g, '$1'], // Links: [text](url) → text
  [/`([^`]+)`/g, '$1'],           // Inline code: `code` → code
  [/^[-*+]\s+/gm, ''],            // List markers: - item → item
  [/^\d+\.\s+/gm, ''],            // Numbered lists: 1. item → item
  [/^>\s+/gm, ''],                // Blockquotes: > text → text
];

/** Minimum sentence length to avoid micro-fragments in TTS */
const MIN_SENTENCE_LENGTH = 10;

/** Maximum sentence length (Polly neural limit) */
const MAX_SENTENCE_LENGTH = 3000;

/**
 * Result of sentence extraction from a streaming text buffer.
 */
export interface SentenceExtraction {
  /** Complete sentences ready for TTS */
  sentences: string[];
  /** Remaining incomplete text that needs more data */
  remaining: string;
}

/**
 * Check if a period at a given position is likely an abbreviation, not a sentence end.
 */
function isAbbreviation(text: string, dotIndex: number): boolean {
  // Find the word before the dot
  let wordStart = dotIndex - 1;
  while (wordStart >= 0 && /[a-zA-Z]/.test(text[wordStart])) {
    wordStart--;
  }
  const word = text.substring(wordStart + 1, dotIndex).toLowerCase();
  return ABBREVIATIONS.has(word);
}

/**
 * Check if a period is part of a decimal number (e.g., "3.14").
 */
function isDecimalNumber(text: string, dotIndex: number): boolean {
  if (dotIndex <= 0 || dotIndex >= text.length - 1) return false;
  return /\d/.test(text[dotIndex - 1]) && /\d/.test(text[dotIndex + 1]);
}

/**
 * Check if a position is inside a URL.
 */
function isInsideUrl(text: string, pos: number): boolean {
  // Look backwards for http:// or https:// or www.
  const before = text.substring(Math.max(0, pos - 100), pos + 1);
  const match = before.match(/https?:\/\/\S*$|www\.\S*$/);
  return match !== null;
}

/**
 * Strip markdown formatting for TTS-friendly plain text.
 */
export function stripMarkdown(text: string): string {
  let result = text;
  for (const [pattern, replacement] of MARKDOWN_PATTERNS) {
    result = result.replace(pattern, replacement);
  }
  return result.trim();
}

/**
 * Replace code blocks with a spoken description.
 */
function replaceCodeBlocks(text: string): string {
  // Replace fenced code blocks with a spoken placeholder
  return text.replace(/```[\s\S]*?```/g, "Here's a code example.");
}

/**
 * Extract complete sentences from a streaming text buffer.
 *
 * Call this incrementally as text_delta events arrive. Pass the returned
 * `remaining` as the start of the next buffer.
 *
 * @param buffer - Accumulated text including previous remaining + new delta
 * @returns Complete sentences and remaining buffer
 *
 * @example
 * ```ts
 * let buffer = '';
 * onTextDelta((delta) => {
 *   buffer += delta;
 *   const { sentences, remaining } = extractSentences(buffer);
 *   buffer = remaining;
 *   for (const sentence of sentences) {
 *     synthesizeAndPlay(sentence);
 *   }
 * });
 * ```
 */
export function extractSentences(buffer: string): SentenceExtraction {
  // First, handle code blocks — replace complete ones with spoken text
  const processed = replaceCodeBlocks(buffer);

  // Check if we're inside an unclosed code block — don't split
  const codeBlockCount = (processed.match(/```/g) || []).length;
  if (codeBlockCount % 2 !== 0) {
    // Inside a code block — wait for it to close
    return { sentences: [], remaining: buffer };
  }

  const sentences: string[] = [];
  let current = '';
  let i = 0;

  while (i < processed.length) {
    const char = processed[i];
    current += char;

    // Check for sentence-ending punctuation
    if (SENTENCE_ENDERS.test(char)) {
      // Skip if it's an abbreviation
      if (char === '.' && isAbbreviation(processed, i)) {
        i++;
        continue;
      }

      // Skip if it's a decimal number
      if (char === '.' && isDecimalNumber(processed, i)) {
        i++;
        continue;
      }

      // Skip if inside a URL
      if (isInsideUrl(processed, i)) {
        i++;
        continue;
      }

      // For Latin punctuation, require followed by space/newline/EOF
      if (!CJK_ENDERS.test(char)) {
        const next = i + 1 < processed.length ? processed[i + 1] : null;
        if (next !== null && next !== ' ' && next !== '\n' && next !== '\r') {
          i++;
          continue;
        }
      }

      // We have a sentence boundary
      const trimmed = stripMarkdown(current.trim());
      if (trimmed.length >= MIN_SENTENCE_LENGTH) {
        // Enforce max length
        if (trimmed.length > MAX_SENTENCE_LENGTH) {
          sentences.push(trimmed.substring(0, MAX_SENTENCE_LENGTH));
        } else {
          sentences.push(trimmed);
        }
        current = '';
      }
      // If too short, keep accumulating
    }

    i++;
  }

  return {
    sentences,
    remaining: current,
  };
}

/**
 * Flush the remaining buffer as a final sentence.
 *
 * Call this when the stream completes to emit any remaining text.
 *
 * @param remaining - The remaining buffer from the last extractSentences call
 * @returns The final sentence (or empty string if too short)
 */
export function flushRemaining(remaining: string): string {
  if (!remaining || !remaining.trim()) return '';

  const cleaned = stripMarkdown(replaceCodeBlocks(remaining).trim());
  if (cleaned.length < MIN_SENTENCE_LENGTH) return '';

  if (cleaned.length > MAX_SENTENCE_LENGTH) {
    return cleaned.substring(0, MAX_SENTENCE_LENGTH);
  }
  return cleaned;
}
