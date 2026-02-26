/**
 * Unit tests for the TSCC API service.
 *
 * Tests the TSCC service layer including:
 * - ``toCamelCase`` snake_case → camelCase conversion for TSCCState
 * - ``snapshotToCamelCase`` conversion for TSCCSnapshot
 * - ``getTSCCState`` URL construction and response mapping
 * - ``createSnapshot`` request body and response mapping
 * - ``listSnapshots`` URL construction and array mapping
 * - ``getSnapshot`` URL construction with snapshot_id
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
  snapshotToCamelCase,
  getTSCCState,
  createSnapshot,
  listSnapshots,
  getSnapshot,
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

const sampleBackendSnapshot = {
  snapshot_id: 'snap-001',
  thread_id: 'thread-abc',
  timestamp: '2026-02-26T10:05:00Z',
  reason: 'plan decomposition',
  lifecycle_state: 'active',
  active_agents: ['SwarmAgent'],
  active_capabilities: { skills: [], mcps: [], tools: [] },
  what_ai_doing: ['Reviewing changes'],
  active_sources: [{ path: 'src/app.py', origin: 'Project' }],
  key_summary: ['Changes look good'],
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
  // snapshotToCamelCase
  // ---------------------------------------------------------------
  describe('snapshotToCamelCase', () => {
    it('should convert all snapshot fields to camelCase', () => {
      const result = snapshotToCamelCase(sampleBackendSnapshot);
      expect(result.snapshotId).toBe('snap-001');
      expect(result.threadId).toBe('thread-abc');
      expect(result.timestamp).toBe('2026-02-26T10:05:00Z');
      expect(result.reason).toBe('plan decomposition');
      expect(result.lifecycleState).toBe('active');
      expect(result.activeAgents).toEqual(['SwarmAgent']);
      expect(result.whatAiDoing).toEqual(['Reviewing changes']);
      expect(result.keySummary).toEqual(['Changes look good']);
    });

    it('should convert nested active_sources in snapshot', () => {
      const result = snapshotToCamelCase(sampleBackendSnapshot);
      expect(result.activeSources).toEqual([
        { path: 'src/app.py', origin: 'Project' },
      ]);
    });

    it('should convert nested active_capabilities in snapshot', () => {
      const result = snapshotToCamelCase(sampleBackendSnapshot);
      expect(result.activeCapabilities.skills).toEqual([]);
      expect(result.activeCapabilities.mcps).toEqual([]);
      expect(result.activeCapabilities.tools).toEqual([]);
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
  // createSnapshot
  // ---------------------------------------------------------------
  describe('createSnapshot', () => {
    it('should call POST with correct URL and body', async () => {
      vi.mocked(api.post).mockResolvedValue({ data: sampleBackendSnapshot });

      await createSnapshot('thread-xyz', 'plan decomposition');

      expect(api.post).toHaveBeenCalledWith(
        '/chat_threads/thread-xyz/snapshots',
        { reason: 'plan decomposition' },
      );
    });

    it('should return camelCase TSCCSnapshot', async () => {
      vi.mocked(api.post).mockResolvedValue({ data: sampleBackendSnapshot });

      const result = await createSnapshot('thread-abc', 'plan decomposition');
      expect(result.snapshotId).toBe('snap-001');
      expect(result.threadId).toBe('thread-abc');
      expect(result.reason).toBe('plan decomposition');
      expect(result.lifecycleState).toBe('active');
    });
  });

  // ---------------------------------------------------------------
  // listSnapshots
  // ---------------------------------------------------------------
  describe('listSnapshots', () => {
    it('should call GET with correct URL', async () => {
      vi.mocked(api.get).mockResolvedValue({ data: [sampleBackendSnapshot] });

      await listSnapshots('thread-xyz');

      expect(api.get).toHaveBeenCalledWith(
        '/chat_threads/thread-xyz/snapshots',
      );
    });

    it('should return array of camelCase TSCCSnapshots', async () => {
      vi.mocked(api.get).mockResolvedValue({
        data: [sampleBackendSnapshot, sampleBackendSnapshot],
      });

      const result = await listSnapshots('thread-abc');
      expect(result).toHaveLength(2);
      expect(result[0].snapshotId).toBe('snap-001');
      expect(result[1].snapshotId).toBe('snap-001');
    });

    it('should return empty array when no snapshots', async () => {
      vi.mocked(api.get).mockResolvedValue({ data: [] });

      const result = await listSnapshots('thread-abc');
      expect(result).toEqual([]);
    });
  });

  // ---------------------------------------------------------------
  // getSnapshot
  // ---------------------------------------------------------------
  describe('getSnapshot', () => {
    it('should call GET with correct URL including snapshot_id', async () => {
      vi.mocked(api.get).mockResolvedValue({ data: sampleBackendSnapshot });

      await getSnapshot('thread-xyz', 'snap-001');

      expect(api.get).toHaveBeenCalledWith(
        '/chat_threads/thread-xyz/snapshots/snap-001',
      );
    });

    it('should return camelCase TSCCSnapshot', async () => {
      vi.mocked(api.get).mockResolvedValue({ data: sampleBackendSnapshot });

      const result = await getSnapshot('thread-abc', 'snap-001');
      expect(result.snapshotId).toBe('snap-001');
      expect(result.threadId).toBe('thread-abc');
      expect(result.activeSources).toEqual([
        { path: 'src/app.py', origin: 'Project' },
      ]);
    });
  });
});
