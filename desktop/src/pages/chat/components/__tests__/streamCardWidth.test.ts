/**
 * Cards in the chat stream take the chat column's width (XG 2026-10-27): no card in a
 * streamed response may fix its width in px / rem; the column decides. jsdom does no
 * layout, so this reads the components' class strings: a fixed `max-w-[Npx]`,
 * `w-[Npx]` (100px and up) or a numeric `maxWidth` / `width` style in a component the
 * stream renders fails, unless the line is in DESIGNED (a deliberate size: a chip, a
 * bubble share of the row).
 *
 * Before: ActFrame (the permission request, AskUserQuestion and the escalation card)
 * had `max-w-[640px]` and stayed 640px wide in a wide chat window.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const SRC = resolve(__dirname, '../../../..');

// every component a streamed assistant / user message renders
const STREAM = [
  'components/common/ActCard.tsx',
  'components/common/AskUserQuestion.tsx',
  'components/common/MarkdownRenderer.tsx',
  'pages/chat/components/AssistantMessageView.tsx',
  'pages/chat/components/ChangesCard.tsx',
  'pages/chat/components/CompactNotice.tsx',
  'pages/chat/components/ContentBlockRenderer.tsx',
  'pages/chat/components/EscalationBlock.tsx',
  'pages/chat/components/InlinePermissionRequest.tsx',
  'pages/chat/components/ItemRows.tsx',
  'pages/chat/components/MessageBubble.tsx',
  'pages/chat/components/NextSteps.tsx',
  'pages/chat/components/StageDocs.tsx',
  'pages/chat/components/TurnCards.tsx',
  'pages/chat/components/TurnPhaseLine.tsx',
  'pages/chat/components/TurnZones.tsx',
  'pages/chat/components/UserMessageView.tsx',
];

// file → substrings of lines whose fixed size is designed, with why
const DESIGNED: Record<string, string[]> = {};

const FIXED = [
  /\bmax-w-\[\d+(\.\d+)?(px|rem)\]/,
  /\b(min-)?w-\[(\d{3,}(\.\d+)?px|\d+(\.\d+)?rem)\]/,
  /\b(maxWidth|width|minWidth)\s*:\s*['"]?\d{3,}/,
];

describe('stream cards follow the chat column width', () => {
  it.each(STREAM)('%s fixes no card width', (file) => {
    const lines = readFileSync(resolve(SRC, file), 'utf8').split('\n');
    const allowed = DESIGNED[file] ?? [];
    const bad = lines
      .map((line, i) => [i + 1, line] as const)
      .filter(([, line]) => FIXED.some((re) => re.test(line)))
      .filter(([, line]) => !allowed.some((part) => line.includes(part)))
      .map(([n, line]) => `${file}:${n}: ${line.trim()}`);
    expect(bad).toEqual([]);
  });
});
