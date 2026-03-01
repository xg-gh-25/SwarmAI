/**
 * Unit Tests for System Service
 *
 * **Feature: swarm-init-status-display**
 * **Validates: Requirements 3.1, 3.2, 3.3**
 *
 * Tests:
 * - getStatus() makes correct API call to /system/status
 * - snake_case to camelCase conversion works correctly
 * - Error propagation on API failure
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { AxiosResponse } from 'axios';

// Mock the api module before importing systemService
vi.mock('../api', () => ({
  default: {
    get: vi.fn(),
  },
}));

// Import after mocking
import api from '../api';
import { systemService, type SystemStatus } from '../system';

describe('System Service - Unit Tests', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  /**
   * Test: getStatus() makes correct API call
   * **Validates: Requirements 3.1**
   */
  describe('getStatus() API call', () => {
    it('should call /system/status endpoint', async () => {
      const mockResponse: AxiosResponse = {
        data: {
          database: { healthy: true },
          agent: { ready: true, name: 'SwarmAgent', skills_count: 3, mcp_servers_count: 2 },
          channel_gateway: { running: true },
          swarm_workspace: { ready: true, name: 'Default', path: '/test' },
          initialized: true,
          timestamp: '2024-01-15T10:30:00.000Z',
        },
        status: 200,
        statusText: 'OK',
        headers: {},
        config: {} as never,
      };

      vi.mocked(api.get).mockResolvedValue(mockResponse);

      await systemService.getStatus();

      expect(api.get).toHaveBeenCalledTimes(1);
      expect(api.get).toHaveBeenCalledWith('/system/status', expect.objectContaining({
        signal: expect.any(AbortSignal),
      }));
    });

    it('should return SystemStatus object on success', async () => {
      const mockResponse: AxiosResponse = {
        data: {
          database: { healthy: true },
          agent: { ready: true, name: 'SwarmAgent', skills_count: 5, mcp_servers_count: 3 },
          channel_gateway: { running: true },
          swarm_workspace: { ready: true, name: 'Default', path: '/test' },
          initialized: true,
          timestamp: '2024-01-15T10:30:00.000Z',
        },
        status: 200,
        statusText: 'OK',
        headers: {},
        config: {} as never,
      };

      vi.mocked(api.get).mockResolvedValue(mockResponse);

      const result = await systemService.getStatus();

      expect(result).toBeDefined();
      expect(result.database).toBeDefined();
      expect(result.agent).toBeDefined();
      expect(result.channelGateway).toBeDefined();
      expect(result.swarmWorkspace).toBeDefined();
      expect(result.initialized).toBe(true);
      expect(result.timestamp).toBe('2024-01-15T10:30:00.000Z');
    });
  });

  /**
   * Test: snake_case to camelCase conversion
   * **Validates: Requirements 3.2**
   */
  describe('Case conversion', () => {
    it('should convert skills_count to skillsCount', async () => {
      const mockResponse: AxiosResponse = {
        data: {
          database: { healthy: true },
          agent: { ready: true, name: 'TestAgent', skills_count: 7, mcp_servers_count: 4 },
          channel_gateway: { running: true },
          swarm_workspace: { ready: true, name: 'Default', path: '/test' },
          initialized: true,
          timestamp: '2024-01-15T10:30:00.000Z',
        },
        status: 200,
        statusText: 'OK',
        headers: {},
        config: {} as never,
      };

      vi.mocked(api.get).mockResolvedValue(mockResponse);

      const result = await systemService.getStatus();

      expect(result.agent.skillsCount).toBe(7);
      expect((result.agent as unknown as Record<string, unknown>)['skills_count']).toBeUndefined();
    });

    it('should convert mcp_servers_count to mcpServersCount', async () => {
      const mockResponse: AxiosResponse = {
        data: {
          database: { healthy: true },
          agent: { ready: true, name: 'TestAgent', skills_count: 3, mcp_servers_count: 10 },
          channel_gateway: { running: true },
          swarm_workspace: { ready: true, name: 'Default', path: '/test' },
          initialized: true,
          timestamp: '2024-01-15T10:30:00.000Z',
        },
        status: 200,
        statusText: 'OK',
        headers: {},
        config: {} as never,
      };

      vi.mocked(api.get).mockResolvedValue(mockResponse);

      const result = await systemService.getStatus();

      expect(result.agent.mcpServersCount).toBe(10);
      expect((result.agent as unknown as Record<string, unknown>)['mcp_servers_count']).toBeUndefined();
    });

    it('should convert channel_gateway to channelGateway', async () => {
      const mockResponse: AxiosResponse = {
        data: {
          database: { healthy: true },
          agent: { ready: true, name: 'TestAgent', skills_count: 0, mcp_servers_count: 0 },
          channel_gateway: { running: false },
          swarm_workspace: { ready: false },
          initialized: false,
          timestamp: '2024-01-15T10:30:00.000Z',
        },
        status: 200,
        statusText: 'OK',
        headers: {},
        config: {} as never,
      };

      vi.mocked(api.get).mockResolvedValue(mockResponse);

      const result = await systemService.getStatus();

      expect(result.channelGateway).toBeDefined();
      expect(result.channelGateway.running).toBe(false);
      expect((result as unknown as Record<string, unknown>)['channel_gateway']).toBeUndefined();
    });

    it('should handle zero counts correctly', async () => {
      const mockResponse: AxiosResponse = {
        data: {
          database: { healthy: true },
          agent: { ready: false, skills_count: 0, mcp_servers_count: 0 },
          channel_gateway: { running: false },
          swarm_workspace: { ready: false },
          initialized: false,
          timestamp: '2024-01-15T10:30:00.000Z',
        },
        status: 200,
        statusText: 'OK',
        headers: {},
        config: {} as never,
      };

      vi.mocked(api.get).mockResolvedValue(mockResponse);

      const result = await systemService.getStatus();

      expect(result.agent.skillsCount).toBe(0);
      expect(result.agent.mcpServersCount).toBe(0);
    });

    it('should handle optional error field in database status', async () => {
      const mockResponse: AxiosResponse = {
        data: {
          database: { healthy: false, error: 'Connection failed' },
          agent: { ready: false, skills_count: 0, mcp_servers_count: 0 },
          channel_gateway: { running: false },
          swarm_workspace: { ready: false },
          initialized: false,
          timestamp: '2024-01-15T10:30:00.000Z',
        },
        status: 200,
        statusText: 'OK',
        headers: {},
        config: {} as never,
      };

      vi.mocked(api.get).mockResolvedValue(mockResponse);

      const result = await systemService.getStatus();

      expect(result.database.healthy).toBe(false);
      expect(result.database.error).toBe('Connection failed');
    });

    it('should handle optional name field in agent status', async () => {
      const mockResponse: AxiosResponse = {
        data: {
          database: { healthy: true },
          agent: { ready: false, name: undefined, skills_count: 0, mcp_servers_count: 0 },
          channel_gateway: { running: false },
          swarm_workspace: { ready: false },
          initialized: false,
          timestamp: '2024-01-15T10:30:00.000Z',
        },
        status: 200,
        statusText: 'OK',
        headers: {},
        config: {} as never,
      };

      vi.mocked(api.get).mockResolvedValue(mockResponse);

      const result = await systemService.getStatus();

      expect(result.agent.ready).toBe(false);
      expect(result.agent.name).toBeUndefined();
    });
  });

  /**
   * Test: Error propagation
   * **Validates: Requirements 3.3**
   */
  describe('Error propagation', () => {
    it('should propagate network errors to caller', async () => {
      const networkError = new Error('Network Error');
      vi.mocked(api.get).mockRejectedValue(networkError);

      await expect(systemService.getStatus()).rejects.toThrow('Network Error');
    });

    it('should propagate API errors to caller', async () => {
      const apiError = new Error('Internal Server Error');
      vi.mocked(api.get).mockRejectedValue(apiError);

      await expect(systemService.getStatus()).rejects.toThrow('Internal Server Error');
    });

    it('should propagate timeout errors to caller', async () => {
      const abortError = new DOMException('The operation was aborted', 'AbortError');
      vi.mocked(api.get).mockRejectedValue(abortError);

      await expect(systemService.getStatus()).rejects.toThrow('The operation was aborted');
    });
  });

  /**
   * Test: Complete response structure
   */
  describe('Complete response structure', () => {
    it('should return all expected fields with correct types', async () => {
      const mockResponse: AxiosResponse = {
        data: {
          database: { healthy: true, error: undefined },
          agent: { ready: true, name: 'SwarmAgent', skills_count: 5, mcp_servers_count: 2 },
          channel_gateway: { running: true },
          swarm_workspace: { ready: true, name: 'Default', path: '/test' },
          initialized: true,
          timestamp: '2024-06-20T14:45:30.000Z',
        },
        status: 200,
        statusText: 'OK',
        headers: {},
        config: {} as never,
      };

      vi.mocked(api.get).mockResolvedValue(mockResponse);

      const result: SystemStatus = await systemService.getStatus();

      // Verify database structure
      expect(typeof result.database.healthy).toBe('boolean');
      expect(result.database.error).toBeUndefined();

      // Verify agent structure
      expect(typeof result.agent.ready).toBe('boolean');
      expect(typeof result.agent.name).toBe('string');
      expect(typeof result.agent.skillsCount).toBe('number');
      expect(typeof result.agent.mcpServersCount).toBe('number');

      // Verify channelGateway structure
      expect(typeof result.channelGateway.running).toBe('boolean');

      // Verify swarmWorkspace structure
      expect(typeof result.swarmWorkspace.ready).toBe('boolean');

      // Verify top-level fields
      expect(typeof result.initialized).toBe('boolean');
      expect(typeof result.timestamp).toBe('string');
    });
  });
});
