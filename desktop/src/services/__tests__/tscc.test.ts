/**
 * Unit tests for the TSCC API service.
 *
 * Tests the TSCC service layer including:
 * - ``toCamelCase`` snake_case → camelCase conversion for TSCCState
 * - ``getTSCCState`` URL construction and response mapping
 * - ``getSystemPromptMetadata`` URL construction and response mapping
 *
 * Testing methodology: unit tests with mocked axios API layer.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';

// Mock api module before importing the module under test
vi.mock('../api', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

import {
  toCamelCase,
  getTSCCState,
  getSystemPromptMetadata,
} from '../tscc';
import api from '../api';

// --- Sample snake_case backend responses ---

const sampleBackendState = {
  thread_id: 'thread-abc',
  project_id: null,
  scope_type: 'workspace',
  last_updated_at: '2026-02-26T10:00:00Z',
  lifecycle_state: 'active',
  live_state: {
    context: {
      scope_label: 'Workspace: SwarmWS (General)',
      thread_title: 'Test Thread',
      mode: null,
    },
    active_agents: ['SwarmAgent'],
    active_capabilities: {
      skills: ['code-review'],
      mcps: ['filesystem'],
      tools: ['read_file'],
    },
    what_ai_doing: ['Analyzing code'],
    active_sources: [
      { path: 'src/main.py', origin: 'Project' },
    ],
    key_summary: ['Initial analysis complete'],
  },
};

const sampleSystemPromptMetadata = {
  files: [
    { filename: 'SWARMAI.md', tokens: 500, truncated: false },
    { filename: 'IDENTITY.md', tokens: 200, truncated: false },
    { filename: 'KNOWLEDGE.md', tokens: 1200, truncated: true },
  ],
  total_tokens: 1900,
  full_text: '# System Prompt\nHello world',
};

describe('TSCC Service - Unit Tests', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // ---------------------------------------------------------------
  // toCamelCase
  // ---------------------------------------------------------------
  describe('toCamelCase', () => {
    it('should convert all top-level snake_case fields', () => {
      const result = toCamelCase(sampleBackendState);
      expect(result.threadId).toBe('thread-abc');
      expect(result.projectId).toBeNull();
      expect(result.scopeType).toBe('workspace');
      expect(result.lastUpdatedAt).toBe('2026-02-26T10:00:00Z');
      expect(result.lifecycleState).toBe('active');
    });

    it('should convert nested live_state fields', () => {
      const result = toCamelCase(sampleBackendState);
      const ls = result.liveState;
      expect(ls.context.scopeLabel).toBe('Workspace: SwarmWS (General)');
      expect(ls.context.threadTitle).toBe('Test Thread');
      expect(ls.context.mode).toBeUndefined();
      expect(ls.activeAgents).toEqual(['SwarmAgent']);
      expect(ls.whatAiDoing).toEqual(['Analyzing code']);
      expect(ls.keySummary).toEqual(['Initial analysis complete']);
    });

    it('should convert nested active_capabilities', () => {
      const result = toCamelCase(sampleBackendState);
      const caps = result.liveState.activeCapabilities;
      expect(caps.skills).toEqual(['code-review']);
      expect(caps.mcps).toEqual(['filesystem']);
      expect(caps.tools).toEqual(['read_file']);
    });

    it('should convert nested active_sources', () => {
      const result = toCamelCase(sampleBackendState);
      expect(result.liveState.activeSources).toEqual([
        { path: 'src/main.py', origin: 'Project' },
      ]);
    });

    it('should handle empty arrays gracefully', () => {
      const data = {
        ...sampleBackendState,
        live_state: {
          ...sampleBackendState.live_state,
          active_agents: [],
          active_sources: [],
          key_summary: [],
          what_ai_doing: [],
        },
      };
      const result = toCamelCase(data);
      expect(result.liveState.activeAgents).toEqual([]);
      expect(result.liveState.activeSources).toEqual([]);
    });
  });

  // ---------------------------------------------------------------
  // getTSCCState
  // ---------------------------------------------------------------
  describe('getTSCCState', () => {
    it('should call GET with correct URL', async () => {
      vi.mocked(api.get).mockResolvedValue({ data: sampleBackendState });
      await getTSCCState('thread-xyz');
      expect(api.get).toHaveBeenCalledWith('/chat_threads/thread-xyz/tscc');
    });

    it('should return camelCase TSCCState', async () => {
      vi.mocked(api.get).mockResolvedValue({ data: sampleBackendState });
      const result = await getTSCCState('thread-abc');
      expect(result.threadId).toBe('thread-abc');
      expect(result.liveState.context.scopeLabel).toBe(
        'Workspace: SwarmWS (General)',
      );
    });
  });

  // ---------------------------------------------------------------
  // getSystemPromptMetadata
  // ---------------------------------------------------------------
  describe('getSystemPromptMetadata', () => {
    it('should call GET with correct URL', async () => {
      vi.mocked(api.get).mockResolvedValue({ data: sampleSystemPromptMetadata });
      await getSystemPromptMetadata('session-123');
      expect(api.get).toHaveBeenCalledWith('/chat/session-123/system-prompt');
    });

    it('should convert snake_case to camelCase', async () => {
      vi.mocked(api.get).mockResolvedValue({ data: sampleSystemPromptMetadata });
      const result = await getSystemPromptMetadata('session-123');
      expect(result.totalTokens).toBe(1900);
      expect(result.fullText).toBe('# System Prompt\nHello world');
      expect(result.files).toHaveLength(3);
    });

    it('should map file metadata correctly', async () => {
      vi.mocked(api.get).mockResolvedValue({ data: sampleSystemPromptMetadata });
      const result = await getSystemPromptMetadata('session-123');
      expect(result.files[0]).toEqual({
        filename: 'SWARMAI.md', tokens: 500, truncated: false,
      });
      expect(result.files[2].truncated).toBe(true);
    });

    it('should handle empty files array', async () => {
      vi.mocked(api.get).mockResolvedValue({
        data: { files: [], total_tokens: 0, full_text: '' },
      });
      const result = await getSystemPromptMetadata('session-empty');
      expect(result.files).toEqual([]);
      expect(result.totalTokens).toBe(0);
      expect(result.fullText).toBe('');
    });
  });
});
