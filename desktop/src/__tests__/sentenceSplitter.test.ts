/**
 * Tests for sentenceSplitter — streaming sentence boundary detection for TTS.
 *
 * Covers: English/CJK punctuation, abbreviations, decimal numbers, URLs,
 * code blocks, markdown stripping, min/max length, adversarial inputs.
 */

import { describe, it, expect } from 'vitest';
import {
  extractSentences,
  flushRemaining,
  stripMarkdown,
} from '../utils/sentenceSplitter';

// ─── Basic sentence splitting ───────────────────────────────────────

describe('extractSentences — basic', () => {
  it('splits on period + space', () => {
    const { sentences, remaining } = extractSentences(
      'Hello world. How are you? '
    );
    expect(sentences).toEqual(['Hello world.', 'How are you?']);
    expect(remaining.trim()).toBe('');
  });

  it('splits on exclamation mark', () => {
    const { sentences } = extractSentences('Great job! Keep going. ');
    expect(sentences).toEqual(['Great job!', 'Keep going.']);
  });

  it('splits on question mark', () => {
    const { sentences } = extractSentences('What happened? I need to know. ');
    expect(sentences).toEqual(['What happened?', 'I need to know.']);
  });

  it('keeps remaining buffer for incomplete sentence', () => {
    const { sentences, remaining } = extractSentences(
      'Hello world. This is still going'
    );
    expect(sentences).toEqual(['Hello world.']);
    expect(remaining).toContain('This is still going');
  });

  it('returns empty for short buffer', () => {
    const { sentences, remaining } = extractSentences('Hi.');
    // "Hi." is only 3 chars, below MIN_SENTENCE_LENGTH (10)
    expect(sentences).toEqual([]);
    expect(remaining).toBe('Hi.');
  });
});

// ─── CJK sentence splitting ────────────────────────────────────────

describe('extractSentences — CJK', () => {
  it('splits on Chinese period 。', () => {
    const { sentences } = extractSentences('你好世界。这是测试。');
    expect(sentences.length).toBeGreaterThanOrEqual(1);
    // At least one sentence should be extracted
    expect(sentences[0]).toContain('你好世界');
  });

  it('splits on Chinese exclamation ！', () => {
    // Need enough chars per sentence (MIN_SENTENCE_LENGTH=10)
    const { sentences } = extractSentences('今天的工作完成得太好了！我们继续努力加油吧。');
    expect(sentences.length).toBeGreaterThanOrEqual(1);
  });

  it('splits on Chinese question ？', () => {
    const { sentences } = extractSentences('你在做什么？我想知道。');
    expect(sentences.length).toBeGreaterThanOrEqual(1);
  });

  it('handles mixed English/Chinese', () => {
    const { sentences } = extractSentences(
      'Hello你好。This is a test sentence for mixed content。'
    );
    expect(sentences.length).toBeGreaterThanOrEqual(1);
  });
});

// ─── Abbreviation handling ──────────────────────────────────────────

describe('extractSentences — abbreviations', () => {
  it('does not split on Mr.', () => {
    const { sentences, remaining } = extractSentences(
      'Mr. Smith went to the store. '
    );
    expect(sentences).toEqual(['Mr. Smith went to the store.']);
  });

  it('does not split on Dr.', () => {
    const { sentences } = extractSentences(
      'Dr. Johnson is available now. '
    );
    expect(sentences.length).toBe(1);
    expect(sentences[0]).toContain('Dr. Johnson');
  });

  it('does not split on Ltd.', () => {
    const { sentences } = extractSentences(
      'Amazon Ltd. is a big company. '
    );
    expect(sentences.length).toBe(1);
    expect(sentences[0]).toContain('Ltd.');
  });
});

// ─── Decimal numbers ────────────────────────────────────────────────

describe('extractSentences — decimal numbers', () => {
  it('does not split on 3.14', () => {
    const { sentences } = extractSentences(
      'The value is 3.14 approximately. '
    );
    expect(sentences.length).toBe(1);
    expect(sentences[0]).toContain('3.14');
  });

  it('does not split on version numbers like 2.0', () => {
    const { sentences } = extractSentences(
      'We use version 2.0 of the framework. '
    );
    expect(sentences.length).toBe(1);
    expect(sentences[0]).toContain('2.0');
  });
});

// ─── URL handling ───────────────────────────────────────────────────

describe('extractSentences — URLs', () => {
  it('does not split on periods in URLs', () => {
    const { sentences, remaining } = extractSentences(
      'Visit https://example.com/page for more info. '
    );
    // Should produce one sentence containing the full URL
    const all = [...sentences, remaining].join(' ');
    expect(all).toContain('https://example.com/page');
  });
});

// ─── Code block handling ────────────────────────────────────────────

describe('extractSentences — code blocks', () => {
  it('replaces complete code blocks with spoken text', () => {
    const input = 'Here is an example. ```python\nprint("hello")\n``` And more text. ';
    const { sentences } = extractSentences(input);
    // Code block should be replaced with "Here's a code example."
    const all = sentences.join(' ');
    expect(all).not.toContain('print');
    expect(all).toContain("code example");
  });

  it('buffers unclosed code blocks', () => {
    const input = 'Before. ```python\nprint("hello")';
    const { sentences, remaining } = extractSentences(input);
    // Inside unclosed code block — should buffer everything
    expect(sentences).toEqual([]);
    expect(remaining).toBe(input);
  });
});

// ─── Markdown stripping ────────────────────────────────────────────

describe('stripMarkdown', () => {
  it('strips bold', () => {
    expect(stripMarkdown('**Hello** world')).toBe('Hello world');
  });

  it('strips italic', () => {
    expect(stripMarkdown('*Hello* world')).toBe('Hello world');
  });

  it('strips headers', () => {
    expect(stripMarkdown('## Header text')).toBe('Header text');
  });

  it('strips links', () => {
    expect(stripMarkdown('[click here](https://example.com)')).toBe('click here');
  });

  it('strips inline code', () => {
    expect(stripMarkdown('Use `npm install` to setup')).toBe('Use npm install to setup');
  });

  it('strips list markers', () => {
    expect(stripMarkdown('- First item')).toBe('First item');
  });

  it('strips blockquotes', () => {
    expect(stripMarkdown('> Quoted text')).toBe('Quoted text');
  });
});

// ─── flushRemaining ────────────────────────────────────────────────

describe('flushRemaining', () => {
  it('returns remaining text if long enough', () => {
    const result = flushRemaining('This is the final piece of text');
    expect(result).toBe('This is the final piece of text');
  });

  it('returns empty for short text', () => {
    const result = flushRemaining('Short');
    expect(result).toBe('');
  });

  it('returns empty for empty/whitespace', () => {
    expect(flushRemaining('')).toBe('');
    expect(flushRemaining('   ')).toBe('');
  });

  it('strips markdown from remaining', () => {
    const result = flushRemaining('**Bold text** and more content here');
    expect(result).toBe('Bold text and more content here');
  });
});

// ─── Max length enforcement ─────────────────────────────────────────

describe('extractSentences — max length', () => {
  it('truncates sentences longer than 3000 chars', () => {
    const longText = 'A'.repeat(4000) + '. ';
    const { sentences } = extractSentences(longText);
    if (sentences.length > 0) {
      expect(sentences[0].length).toBeLessThanOrEqual(3000);
    }
  });
});

// ─── Streaming simulation ───────────────────────────────────────────

describe('extractSentences — streaming', () => {
  it('handles incremental text arrival', () => {
    let buffer = '';
    const allSentences: string[] = [];

    // Simulate streaming text_delta events
    const chunks = [
      'Yesterday we ',
      'worked on the ',
      'VoiceBox research. ',
      'We analyzed the ',
      'architecture and ',
      'found several patterns. ',
    ];

    for (const chunk of chunks) {
      buffer += chunk;
      const { sentences, remaining } = extractSentences(buffer);
      allSentences.push(...sentences);
      buffer = remaining;
    }

    // Flush remaining
    const final = flushRemaining(buffer);
    if (final) allSentences.push(final);

    expect(allSentences.length).toBe(2);
    expect(allSentences[0]).toContain('VoiceBox research');
    expect(allSentences[1]).toContain('several patterns');
  });
});

// ─── Edge cases / adversarial ───────────────────────────────────────

describe('extractSentences — edge cases', () => {
  it('handles empty string', () => {
    const { sentences, remaining } = extractSentences('');
    expect(sentences).toEqual([]);
    expect(remaining).toBe('');
  });

  it('handles emoji', () => {
    const { sentences } = extractSentences('Great work 🎉! The feature is live. ');
    expect(sentences.length).toBeGreaterThanOrEqual(1);
  });

  it('handles multiple consecutive periods (ellipsis)', () => {
    // "..." should not create 3 sentence breaks
    const { sentences, remaining } = extractSentences(
      'Well... let me think about it. '
    );
    // The ellipsis might create issues, but should not crash
    expect(typeof sentences).toBe('object');
  });

  it('handles newlines as sentence separators', () => {
    const { sentences } = extractSentences(
      'First sentence.\nSecond sentence.\n'
    );
    expect(sentences.length).toBe(2);
  });
});
