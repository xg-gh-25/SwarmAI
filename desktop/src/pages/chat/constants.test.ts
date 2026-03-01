/**
 * Property-Based Tests for Chat Constants
 *
 * **Feature: chat-constants**
 * **Property 1: Welcome Message Generation**
 * **Property 2: Workspace Change Message Generation**
 * **Validates: Message structure and content consistency**
 *
 * These tests validate the message generator functions.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import * as fc from 'fast-check';
import {
  createWelcomeMessage,
  createWorkspaceChangeMessage,
  MS_PER_DAY,
  SLASH_COMMANDS,
  TIME_GROUP_LABEL_KEYS,
  OPEN_TABS_STORAGE_KEY,
  ACTIVE_TAB_STORAGE_KEY,
} from './constants';

// ============== Property-Based Tests ==============

describe('Chat Constants - Property-Based Tests', () => {
  /**
   * Property 1: Welcome Message Generation
   * **Feature: chat-constants, Property 1: Welcome Message Generation**
   *
   * For any call to createWelcomeMessage, the result SHALL be a valid Message
   * object with correct structure.
   */
  describe('Feature: chat-constants, Property 1: Welcome Message Generation', () => {
    beforeEach(() => {
      vi.useFakeTimers();
      vi.setSystemTime(new Date('2025-02-19T12:00:00.000Z'));
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    it('should create message with valid structure', () => {
      fc.assert(
        fc.property(fc.constant(undefined), () => {
          const message = createWelcomeMessage();

          // Property: Message SHALL have required fields
          expect(message).toHaveProperty('id');
          expect(message).toHaveProperty('role');
          expect(message).toHaveProperty('content');
          expect(message).toHaveProperty('timestamp');
        }),
        { numRuns: 10 }
      );
    });

    it('should create message with assistant role', () => {
      fc.assert(
        fc.property(fc.constant(undefined), () => {
          const message = createWelcomeMessage();

          // Property: Role SHALL be 'assistant'
          expect(message.role).toBe('assistant');
        }),
        { numRuns: 10 }
      );
    });

    it('should create message with text content block', () => {
      fc.assert(
        fc.property(fc.constant(undefined), () => {
          const message = createWelcomeMessage();

          // Property: Content SHALL have at least one text block
          expect(message.content.length).toBeGreaterThan(0);
          expect(message.content[0].type).toBe('text');
        }),
        { numRuns: 10 }
      );
    });

    it('should use custom text when provided', () => {
      fc.assert(
        fc.property(fc.string({ minLength: 1, maxLength: 500 }), (customText) => {
          const message = createWelcomeMessage(customText);

          // Property: Custom text SHALL be used in content
          expect(message.content[0]).toHaveProperty('text', customText);
        }),
        { numRuns: 100 }
      );
    });

    it('should use default welcome text when no custom text provided', () => {
      fc.assert(
        fc.property(fc.constant(undefined), () => {
          const message = createWelcomeMessage();

          // Property: Default text SHALL contain welcome message
          const textContent = message.content[0] as { type: string; text: string };
          expect(textContent.text).toContain('Welcome to SwarmAI');
        }),
        { numRuns: 10 }
      );
    });

    it('should generate unique IDs for each message', () => {
      fc.assert(
        fc.property(fc.integer({ min: 2, max: 10 }), (count) => {
          // Advance time between each message creation
          const messages = [];
          for (let i = 0; i < count; i++) {
            vi.advanceTimersByTime(1);
            messages.push(createWelcomeMessage());
          }

          // Property: Each message SHALL have a unique ID
          const ids = messages.map((m) => m.id);
          const uniqueIds = new Set(ids);
          expect(uniqueIds.size).toBe(count);
        }),
        { numRuns: 50 }
      );
    });

    it('should generate valid ISO timestamp', () => {
      fc.assert(
        fc.property(fc.constant(undefined), () => {
          const message = createWelcomeMessage();

          // Property: Timestamp SHALL be valid ISO string
          const date = new Date(message.timestamp);
          expect(isNaN(date.getTime())).toBe(false);
        }),
        { numRuns: 10 }
      );
    });
  });

  /**
   * Property 2: Workspace Change Message Generation
   * **Feature: chat-constants, Property 2: Workspace Change Message Generation**
   *
   * For any workspace change, createWorkspaceChangeMessage SHALL generate
   * a message indicating the workspace context change.
   */
  describe('Feature: chat-constants, Property 2: Workspace Change Message Generation', () => {
    beforeEach(() => {
      vi.useFakeTimers();
      vi.setSystemTime(new Date('2025-02-19T12:00:00.000Z'));
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    it('should include workspace name when provided', () => {
      fc.assert(
        fc.property(
          fc.string({ minLength: 1, maxLength: 50 }),
          fc.string({ minLength: 1, maxLength: 200 }),
          (workspaceName, workspacePath) => {
            const message = createWorkspaceChangeMessage(workspaceName, workspacePath);

            // Property: Message SHALL contain workspace name
            const textContent = message.content[0] as { type: string; text: string };
            expect(textContent.text).toContain(workspaceName);
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should include workspace path when provided', () => {
      fc.assert(
        fc.property(
          fc.string({ minLength: 1, maxLength: 50 }),
          fc.string({ minLength: 1, maxLength: 200 }),
          (workspaceName, workspacePath) => {
            const message = createWorkspaceChangeMessage(workspaceName, workspacePath);

            // Property: Message SHALL contain workspace path
            const textContent = message.content[0] as { type: string; text: string };
            expect(textContent.text).toContain(workspacePath);
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should indicate workspace cleared when no name provided', () => {
      fc.assert(
        fc.property(fc.constant(undefined), () => {
          const message = createWorkspaceChangeMessage();

          // Property: Message SHALL indicate workspace cleared
          const textContent = message.content[0] as { type: string; text: string };
          expect(textContent.text).toContain('Workspace cleared');
        }),
        { numRuns: 10 }
      );
    });

    it('should include welcome message after workspace context', () => {
      fc.assert(
        fc.property(
          fc.option(fc.string({ minLength: 1, maxLength: 50 }), { nil: undefined }),
          fc.option(fc.string({ minLength: 1, maxLength: 200 }), { nil: undefined }),
          (workspaceName, workspacePath) => {
            const message = createWorkspaceChangeMessage(workspaceName, workspacePath);

            // Property: Message SHALL include welcome text
            const textContent = message.content[0] as { type: string; text: string };
            expect(textContent.text).toContain('Welcome to SwarmAI');
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should have valid message structure', () => {
      fc.assert(
        fc.property(
          fc.option(fc.string({ minLength: 1, maxLength: 50 }), { nil: undefined }),
          (workspaceName) => {
            const message = createWorkspaceChangeMessage(workspaceName);

            // Property: Message SHALL have valid structure
            expect(message.role).toBe('assistant');
            expect(message.content.length).toBeGreaterThan(0);
            expect(message.content[0].type).toBe('text');
          }
        ),
        { numRuns: 50 }
      );
    });
  });

  /**
   * Property 3: Constants Integrity
   * **Feature: chat-constants, Property 3: Constants Integrity**
   *
   * All exported constants SHALL have correct values and types.
   */
  describe('Feature: chat-constants, Property 3: Constants Integrity', () => {
    it('should have correct MS_PER_DAY value', () => {
      fc.assert(
        fc.property(fc.constant(null), () => {
          // Property: MS_PER_DAY SHALL equal 24 * 60 * 60 * 1000
          expect(MS_PER_DAY).toBe(86400000);
          expect(MS_PER_DAY).toBe(24 * 60 * 60 * 1000);
        }),
        { numRuns: 1 }
      );
    });

    it('should have all required slash commands', () => {
      fc.assert(
        fc.property(fc.constant(null), () => {
          // Property: SLASH_COMMANDS SHALL contain expected commands
          const commandNames = SLASH_COMMANDS.map((c) => c.name);
          expect(commandNames).toContain('/clear');
          expect(commandNames).toContain('/compact');
          expect(commandNames).toContain('/plugin list');
        }),
        { numRuns: 1 }
      );
    });

    it('should have all slash commands with descriptions', () => {
      fc.assert(
        fc.property(fc.constant(null), () => {
          // Property: Each slash command SHALL have a description
          for (const command of SLASH_COMMANDS) {
            expect(command.name.length).toBeGreaterThan(0);
            expect(command.description.length).toBeGreaterThan(0);
          }
        }),
        { numRuns: 1 }
      );
    });

    it('should have all time group label keys', () => {
      fc.assert(
        fc.property(fc.constant(null), () => {
          // Property: TIME_GROUP_LABEL_KEYS SHALL have all required groups
          expect(TIME_GROUP_LABEL_KEYS).toHaveProperty('today');
          expect(TIME_GROUP_LABEL_KEYS).toHaveProperty('yesterday');
          expect(TIME_GROUP_LABEL_KEYS).toHaveProperty('thisWeek');
          expect(TIME_GROUP_LABEL_KEYS).toHaveProperty('thisMonth');
          expect(TIME_GROUP_LABEL_KEYS).toHaveProperty('older');
        }),
        { numRuns: 1 }
      );
    });

    it('should have correct tab persistence localStorage keys', () => {
      fc.assert(
        fc.property(fc.constant(null), () => {
          // Property: Tab persistence keys SHALL have correct values
          expect(OPEN_TABS_STORAGE_KEY).toBe('swarmAI_openTabs');
          expect(ACTIVE_TAB_STORAGE_KEY).toBe('swarmAI_activeTabId');
        }),
        { numRuns: 1 }
      );
    });
  });
});
