#!/usr/bin/env node
/**
 * Context Window Monitor for SwarmAI
 *
 * Estimates current session's context window usage by analyzing the
 * transcript .jsonl file. Returns structured status for the agent to act on.
 *
 * Usage: node context-check.mjs [--projects-dir <path>] [--window <tokens>]
 *
 * Output: JSON { tokensEst, pct, level, message, details }
 *   level: "ok" | "warn" | "critical"
 */

import { readFileSync, readdirSync, statSync } from 'fs';
import { join } from 'path';

// --- Config ---
const DEFAULTS = {
  // Claude Opus context window
  windowTokens: 200_000,
  // System prompt + skills baseline (estimated)
  baselineTokens: 40_000,
  // Thresholds (fraction of window)
  warnPct: 70,
  criticalPct: 85,
  // Default projects dir
  projectsDir: join(
    process.env.HOME,
    '.claude/projects/-Users-gawan--swarm-ai-SwarmWS'
  ),
};

// --- Parse args ---
const args = process.argv.slice(2);
let projectsDir = DEFAULTS.projectsDir;
let windowTokens = DEFAULTS.windowTokens;

for (let i = 0; i < args.length; i++) {
  if (args[i] === '--projects-dir' && args[i + 1]) projectsDir = args[++i];
  if (args[i] === '--window' && args[i + 1]) windowTokens = parseInt(args[++i]);
}

// --- Find latest transcript ---
function findLatestTranscript(dir) {
  try {
    const files = readdirSync(dir)
      .filter(f => f.endsWith('.jsonl'))
      .map(f => {
        const path = join(dir, f);
        const stat = statSync(path);
        return { path, mtime: stat.mtimeMs, size: stat.size };
      })
      .sort((a, b) => b.mtime - a.mtime);
    return files[0] || null;
  } catch {
    return null;
  }
}

// --- Analyze transcript ---
function analyzeTranscript(filePath) {
  const lines = readFileSync(filePath, 'utf-8').split('\n').filter(Boolean);

  let totalContentChars = 0;
  let userMessages = 0;
  let assistantMessages = 0;
  let toolUseBlocks = 0;
  let toolResultBlocks = 0;
  let systemMessages = 0;
  let lastCompactionIdx = -1;

  // First pass: find last compaction point (summary injection)
  for (let i = 0; i < lines.length; i++) {
    try {
      const obj = JSON.parse(lines[i]);
      // Detect compaction: a user message containing the compaction summary marker
      if (obj.type === 'user') {
        const content = obj.message?.content;
        if (typeof content === 'string' && content.includes('continued from a previous conversation that ran out of context')) {
          lastCompactionIdx = i;
        } else if (Array.isArray(content)) {
          for (const block of content) {
            if (block?.text?.includes('continued from a previous conversation that ran out of context')) {
              lastCompactionIdx = i;
              break;
            }
          }
        }
      }
    } catch { /* skip malformed lines */ }
  }

  // Second pass: count from after last compaction (or from start)
  const startIdx = lastCompactionIdx >= 0 ? lastCompactionIdx : 0;

  for (let i = startIdx; i < lines.length; i++) {
    try {
      const obj = JSON.parse(lines[i]);
      const type = obj.type;

      // Skip progress events (streaming chunks - duplicated in assistant messages)
      if (type === 'progress' || type === 'queue-operation' || type === 'last-prompt') continue;

      const msg = obj.message;
      if (!msg) continue;

      const role = msg.role;
      const content = msg.content;

      if (role === 'user') userMessages++;
      else if (role === 'assistant') assistantMessages++;
      else if (role === 'system') systemMessages++;

      // Count content characters
      if (typeof content === 'string') {
        totalContentChars += content.length;
      } else if (Array.isArray(content)) {
        for (const block of content) {
          if (!block) continue;
          if (block.type === 'text' && typeof block.text === 'string') {
            totalContentChars += block.text.length;
          } else if (block.type === 'tool_use') {
            toolUseBlocks++;
            // Tool input is JSON, count its string representation
            const inputStr = typeof block.input === 'string'
              ? block.input
              : JSON.stringify(block.input || '');
            totalContentChars += inputStr.length;
            // Tool name + id overhead
            totalContentChars += (block.name?.length || 0) + 50;
          } else if (block.type === 'tool_result') {
            toolResultBlocks++;
            if (typeof block.content === 'string') {
              totalContentChars += block.content.length;
            } else if (Array.isArray(block.content)) {
              for (const sub of block.content) {
                if (sub?.text) totalContentChars += sub.text.length;
              }
            }
            totalContentChars += 50; // overhead
          }
        }
      }
    } catch { /* skip */ }
  }

  // Token estimation:
  // - English: ~4 chars/token, Chinese: ~1.5 chars/token
  // - Mixed content: use ~3 chars/token as compromise
  // - Add baseline for system prompts + skill injections
  const contentTokens = Math.ceil(totalContentChars / 3);
  const totalTokensEst = contentTokens + DEFAULTS.baselineTokens;

  return {
    totalContentChars,
    contentTokens,
    totalTokensEst,
    userMessages,
    assistantMessages,
    systemMessages,
    toolUseBlocks,
    toolResultBlocks,
    compacted: lastCompactionIdx >= 0,
    activeFromLine: startIdx,
  };
}

// --- Main ---
const transcript = findLatestTranscript(projectsDir);

if (!transcript) {
  console.log(JSON.stringify({
    tokensEst: 0,
    pct: 0,
    level: 'ok',
    message: 'No active transcript found',
    details: {},
  }));
  process.exit(0);
}

const stats = analyzeTranscript(transcript.path);
const pct = Math.round((stats.totalTokensEst / windowTokens) * 100);

let level = 'ok';
let message = '';

if (pct >= DEFAULTS.criticalPct) {
  level = 'critical';
  message = `Context ${pct}% full (~${Math.round(stats.totalTokensEst/1000)}K/${windowTokens/1000}K tokens). Recommend: save context and start new session NOW.`;
} else if (pct >= DEFAULTS.warnPct) {
  level = 'warn';
  message = `Context ${pct}% full (~${Math.round(stats.totalTokensEst/1000)}K/${windowTokens/1000}K tokens). Consider wrapping up or saving context soon.`;
} else {
  message = `Context ${pct}% full (~${Math.round(stats.totalTokensEst/1000)}K/${windowTokens/1000}K tokens). Plenty of room.`;
}

const output = {
  tokensEst: stats.totalTokensEst,
  pct,
  level,
  message,
  details: {
    contentChars: stats.totalContentChars,
    contentTokens: stats.contentTokens,
    baselineTokens: DEFAULTS.baselineTokens,
    userMessages: stats.userMessages,
    assistantMessages: stats.assistantMessages,
    toolCalls: stats.toolUseBlocks,
    toolResults: stats.toolResultBlocks,
    compacted: stats.compacted,
    transcriptFile: transcript.path,
    transcriptSize: transcript.size,
  },
};

console.log(JSON.stringify(output, null, 2));
