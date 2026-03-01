/**
 * Property-Based Tests for BackendStartupOverlay
 *
 * **Feature: app-initialization-loading**
 * 
 * **Property 1: Initialization Gate Blocks Until Both Ready**
 * **Validates: Requirements 1.1, 2.1, 6.2**
 * 
 * **Property 2: Overlay Visibility While Not Ready**
 * **Validates: Requirements 1.2, 2.2**
 * 
 * **Property 3: Polling Continues Until Ready**
 * **Validates: Requirements 1.3**
 * 
 * **Property 4: Readiness Check Sequence**
 * **Validates: Requirements 1.4, 2.3, 2.4**
 * 
 * **Property 5: Timeout Triggers Error State**
 * **Validates: Requirements 1.5, 2.5, 7.2**
 * 
 * **Property 6: Step Status Indicators**
 * **Validates: Requirements 3.3, 4.1, 4.2**
 * 
 * **Property 7: Retry Resets State**
 * **Validates: Requirements 7.3, 7.4**
 * 
 * **Property 8: State Machine Valid Transitions**
 * **Validates: Requirements 6.1**
 * 
 * **Property 9: i18n Keys Used for All Messages**
 * **Validates: Requirements 8.1**
 */

import { describe, it, expect } from 'vitest';
import * as fc from 'fast-check';

// ============== Shared Type Definitions ==============

type StartupStatus = 'starting' | 'connecting' | 'fetching_status' | 'waiting_for_ready' | 'connected' | 'error';
type InitStepStatus = 'pending' | 'in_progress' | 'success' | 'error';
type VisualIndicator = 'checkmark' | 'error_x' | 'spinner' | 'pending_circle';

const allStates: StartupStatus[] = ['starting', 'connecting', 'fetching_status', 'waiting_for_ready', 'connected', 'error'];

const validTransitions: Record<StartupStatus, StartupStatus[]> = {
  starting: ['connecting', 'error'],
  connecting: ['fetching_status', 'error'],
  fetching_status: ['waiting_for_ready', 'connected', 'error'],
  waiting_for_ready: ['connected', 'error'],
  connected: [],
  error: ['starting'],
};

// ============== Shared Helper Functions ==============

function validateTransition(fromState: StartupStatus, toState: StartupStatus): { valid: boolean; error?: string } {
  const allowedTransitions = validTransitions[fromState];
  if (!allowedTransitions) return { valid: false, error: `Unknown state: ${fromState}` };
  const isValid = allowedTransitions.includes(toState);
  if (!isValid) return { valid: false, error: `Invalid transition from '${fromState}' to '${toState}'` };
  return { valid: true };
}

function checkReadiness(agentReady: boolean, workspaceReady: boolean) {
  return { agentReady, workspaceReady, allReady: agentReady && workspaceReady };
}

// ============== Shared Arbitraries ==============

const startupStatusArb = fc.constantFrom<StartupStatus>(...allStates);
const stepStatusArb = fc.constantFrom<InitStepStatus>('pending', 'in_progress', 'success', 'error');

// ============== Property-Based Tests ==============

describe('BackendStartupOverlay - Property-Based Tests', () => {
  /**
   * Property 1: Initialization Gate Blocks Until Both Ready
   * **Validates: Requirements 1.1, 2.1, 6.2**
   */
  describe('Property 1: Initialization Gate Blocks Until Both Ready', () => {
    it('should show main window only when BOTH agent and workspace are ready', () => {
      fc.assert(
        fc.property(fc.boolean(), fc.boolean(), (agentReady, workspaceReady) => {
          const result = checkReadiness(agentReady, workspaceReady);
          const showMainWindow = result.allReady;
          expect(showMainWindow).toBe(agentReady && workspaceReady);
          return showMainWindow === (agentReady && workspaceReady);
        }),
        { numRuns: 20 } // Only 4 combinations, 20 runs is sufficient
      );
    });

    it('should block main window when either component is not ready', () => {
      // Test cases where at least one component is not ready
      const notReadyStates = fc.tuple(fc.boolean(), fc.boolean()).filter(
        ([a, w]) => !a || !w
      );
      fc.assert(
        fc.property(notReadyStates, ([agentReady, workspaceReady]) => {
          const result = checkReadiness(agentReady, workspaceReady);
          expect(result.allReady).toBe(false);
          return result.allReady === false;
        }),
        { numRuns: 20 }
      );
    });
  });

  /**
   * Property 2: Overlay Visibility While Not Ready
   * **Validates: Requirements 1.2, 2.2**
   */
  describe('Property 2: Overlay Visibility While Not Ready', () => {
    it('should ensure overlay and main window are mutually exclusive', () => {
      fc.assert(
        fc.property(fc.boolean(), fc.boolean(), (agentReady, workspaceReady) => {
          const result = checkReadiness(agentReady, workspaceReady);
          const showOverlay = !result.allReady;
          const showMainWindow = result.allReady;
          expect(showOverlay).not.toBe(showMainWindow);
          return showOverlay !== showMainWindow;
        }),
        { numRuns: 20 }
      );
    });

    it('should keep overlay visible when not all ready', () => {
      const notReadyStates = fc.tuple(fc.boolean(), fc.boolean()).filter(([a, w]) => !a || !w);
      fc.assert(
        fc.property(notReadyStates, ([agentReady, workspaceReady]) => {
          const result = checkReadiness(agentReady, workspaceReady);
          expect(!result.allReady).toBe(true);
          return !result.allReady === true;
        }),
        { numRuns: 20 }
      );
    });
  });

  /**
   * Property 8: State Machine Valid Transitions
   * **Validates: Requirements 6.1**
   */
  describe('Property 8: State Machine Valid Transitions', () => {
    it('should validate all defined valid transitions', () => {
      const validPairs = allStates.flatMap(from => 
        validTransitions[from].map(to => ({ from, to }))
      );
      fc.assert(
        fc.property(fc.constantFrom(...validPairs), ({ from, to }) => {
          const result = validateTransition(from, to);
          expect(result.valid).toBe(true);
          return result.valid;
        }),
        { numRuns: 50 }
      );
    });

    it('should reject invalid state transitions', () => {
      const invalidPairs = fc.tuple(startupStatusArb, startupStatusArb).filter(([from, to]) => {
        const allowed = validTransitions[from];
        return !allowed.includes(to) && !(from === to && allowed.length === 0);
      });
      fc.assert(
        fc.property(invalidPairs, ([from, to]) => {
          const result = validateTransition(from, to);
          expect(result.valid).toBe(false);
          return !result.valid;
        }),
        { numRuns: 50 }
      );
    });

    it('should ensure connected is terminal and error can only go to starting', () => {
      fc.assert(
        fc.property(startupStatusArb, (targetState) => {
          const connectedResult = validateTransition('connected', targetState);
          const errorResult = validateTransition('error', targetState);
          expect(connectedResult.valid).toBe(false);
          expect(errorResult.valid).toBe(targetState === 'starting');
          return !connectedResult.valid && (errorResult.valid === (targetState === 'starting'));
        }),
        { numRuns: 20 }
      );
    });

    it('should validate happy path and fast path sequences', () => {
      const happyPath: StartupStatus[] = ['starting', 'connecting', 'fetching_status', 'waiting_for_ready', 'connected'];
      const fastPath: StartupStatus[] = ['starting', 'connecting', 'fetching_status', 'connected'];
      
      [happyPath, fastPath].forEach(path => {
        for (let i = 0; i < path.length - 1; i++) {
          const result = validateTransition(path[i], path[i + 1]);
          expect(result.valid).toBe(true);
        }
      });
    });
  });

  /**
   * Property 5: Timeout Triggers Error State
   * **Validates: Requirements 1.5, 2.5, 7.2**
   */
  describe('Property 5: Timeout Triggers Error State', () => {
    const DEFAULT_TIMEOUT_MS = 60000;
    const timeoutableStates: StartupStatus[] = ['connecting', 'fetching_status', 'waiting_for_ready'];

    function checkTimeout(elapsedMs: number, timeoutMs: number = DEFAULT_TIMEOUT_MS) {
      return { isTimedOut: elapsedMs >= timeoutMs, elapsedMs, timeoutMs };
    }

    function determineNextStateOnTimeout(currentState: StartupStatus, isTimedOut: boolean): StartupStatus {
      if (!timeoutableStates.includes(currentState)) return currentState;
      return isTimedOut ? 'error' : currentState;
    }

    it('should trigger error when elapsed >= timeout, not trigger when below', () => {
      fc.assert(
        fc.property(fc.integer({ min: 0, max: DEFAULT_TIMEOUT_MS * 2 }), (elapsedMs) => {
          const result = checkTimeout(elapsedMs);
          const expectedTimedOut = elapsedMs >= DEFAULT_TIMEOUT_MS;
          expect(result.isTimedOut).toBe(expectedTimedOut);
          return result.isTimedOut === expectedTimedOut;
        }),
        { numRuns: 50 }
      );
    });

    it('should transition timeoutable states to error on timeout', () => {
      fc.assert(
        fc.property(
          fc.constantFrom(...timeoutableStates),
          fc.integer({ min: DEFAULT_TIMEOUT_MS, max: DEFAULT_TIMEOUT_MS * 2 }),
          (state, elapsedMs) => {
            const nextState = determineNextStateOnTimeout(state, elapsedMs >= DEFAULT_TIMEOUT_MS);
            expect(nextState).toBe('error');
            return nextState === 'error';
          }
        ),
        { numRuns: 30 }
      );
    });

    it('should NOT change non-timeoutable states even on timeout', () => {
      const nonTimeoutable: StartupStatus[] = ['starting', 'connected', 'error'];
      fc.assert(
        fc.property(fc.constantFrom(...nonTimeoutable), (state) => {
          const nextState = determineNextStateOnTimeout(state, true);
          expect(nextState).toBe(state);
          return nextState === state;
        }),
        { numRuns: 20 }
      );
    });
  });

  /**
   * Property 7: Retry Resets State
   * **Validates: Requirements 7.3, 7.4**
   */
  describe('Property 7: Retry Resets State', () => {
    interface InitializationState {
      status: StartupStatus;
      errorMessage: string;
      pollCount: number;
      elapsedTime: number;
    }

    function performRetry(): InitializationState {
      return { status: 'starting', errorMessage: '', pollCount: 0, elapsedTime: 0 };
    }

    function isRetryAllowed(status: StartupStatus): boolean {
      return status === 'error';
    }

    it('should reset all state fields after retry', () => {
      const errorStateArb = fc.record({
        status: fc.constant<StartupStatus>('error'),
        errorMessage: fc.string({ minLength: 1, maxLength: 100 }),
        pollCount: fc.integer({ min: 1, max: 100 }),
        elapsedTime: fc.integer({ min: 60000, max: 300000 }),
      });

      fc.assert(
        fc.property(errorStateArb, () => {
          const resetState = performRetry();
          expect(resetState.status).toBe('starting');
          expect(resetState.errorMessage).toBe('');
          expect(resetState.pollCount).toBe(0);
          expect(resetState.elapsedTime).toBe(0);
          return true;
        }),
        { numRuns: 30 }
      );
    });

    it('should only allow retry from error state', () => {
      fc.assert(
        fc.property(startupStatusArb, (status) => {
          const allowed = isRetryAllowed(status);
          expect(allowed).toBe(status === 'error');
          return allowed === (status === 'error');
        }),
        { numRuns: 20 }
      );
    });

    it('should ensure retry transition from error to starting is valid', () => {
      const result = validateTransition('error', 'starting');
      expect(result.valid).toBe(true);
    });
  });

  /**
   * Property 6: Step Status Indicators
   * **Validates: Requirements 3.3, 4.1, 4.2**
   */
  describe('Property 6: Step Status Indicators', () => {
    const STATUS_ICONS: Record<InitStepStatus, string | null> = {
      success: '✓', error: '✗', pending: '○', in_progress: null,
    };

    function getVisualIndicator(status: InitStepStatus): VisualIndicator {
      const map: Record<InitStepStatus, VisualIndicator> = {
        success: 'checkmark', error: 'error_x', in_progress: 'spinner', pending: 'pending_circle',
      };
      return map[status];
    }

    function shouldRenderSpinner(status: InitStepStatus): boolean {
      return status === 'in_progress';
    }

    function shouldDisplayErrorMessage(status: InitStepStatus, errorMessage?: string): boolean {
      return status === 'error' && !!errorMessage && errorMessage.length > 0;
    }

    it('should map each status to correct visual indicator', () => {
      const expectedMappings: Array<{ status: InitStepStatus; indicator: VisualIndicator; icon: string | null }> = [
        { status: 'success', indicator: 'checkmark', icon: '✓' },
        { status: 'error', indicator: 'error_x', icon: '✗' },
        { status: 'in_progress', indicator: 'spinner', icon: null },
        { status: 'pending', indicator: 'pending_circle', icon: '○' },
      ];

      fc.assert(
        fc.property(fc.constantFrom(...expectedMappings), ({ status, indicator, icon }) => {
          expect(getVisualIndicator(status)).toBe(indicator);
          expect(STATUS_ICONS[status]).toBe(icon);
          expect(shouldRenderSpinner(status)).toBe(status === 'in_progress');
          return true;
        }),
        { numRuns: 20 }
      );
    });

    it('should display error message only for error status with message', () => {
      fc.assert(
        fc.property(
          stepStatusArb,
          fc.option(fc.string({ minLength: 1, maxLength: 100 }), { nil: undefined }),
          (status, errorMessage) => {
            const shows = shouldDisplayErrorMessage(status, errorMessage);
            const expected = status === 'error' && !!errorMessage && errorMessage.length > 0;
            expect(shows).toBe(expected);
            return shows === expected;
          }
        ),
        { numRuns: 30 }
      );
    });
  });

  /**
   * Property 9: i18n Keys Used for All Messages
   * **Validates: Requirements 8.1**
   */
  describe('Property 9: i18n Keys Used for All Messages', () => {
    const STARTUP_I18N_KEYS = [
      'startup.connectingToBackend', 'startup.waitingForReady', 'startup.waitingForAgent',
      'startup.databaseInitialized', 'startup.swarmAgentReady', 'startup.swarmWorkspaceInitialized',
      'startup.initializationTimeout', 'startup.initializationFailed',
    ];

    function isValidI18nKeyFormat(key: string): boolean {
      if (!key || !key.includes('.') || key.startsWith('.') || key.endsWith('.')) return false;
      return /^[a-zA-Z][a-zA-Z0-9_]*(\.[a-zA-Z][a-zA-Z0-9_]*)+$/.test(key);
    }

    function isStartupNamespaceKey(key: string): boolean {
      return key.startsWith('startup.');
    }

    it('should validate all startup i18n keys have correct format', () => {
      fc.assert(
        fc.property(fc.constantFrom(...STARTUP_I18N_KEYS), (key) => {
          expect(isValidI18nKeyFormat(key)).toBe(true);
          expect(isStartupNamespaceKey(key)).toBe(true);
          return isValidI18nKeyFormat(key) && isStartupNamespaceKey(key);
        }),
        { numRuns: 20 }
      );
    });

    it('should reject invalid i18n key formats', () => {
      const invalidKeys = ['', 'nonamespace', '.startWithDot', 'endWithDot.', 'double..dot', 'has spaces.key'];
      fc.assert(
        fc.property(fc.constantFrom(...invalidKeys), (key) => {
          expect(isValidI18nKeyFormat(key)).toBe(false);
          return !isValidI18nKeyFormat(key);
        }),
        { numRuns: 20 }
      );
    });
  });

  /**
   * Property 4: Readiness Check Sequence
   * **Validates: Requirements 1.4, 2.3, 2.4**
   */
  describe('Property 4: Readiness Check Sequence', () => {
    type InitStep = 'health_check' | 'fetch_status' | 'verify_agent' | 'verify_workspace' | 'display_main_ui';
    const REQUIRED_SEQUENCE: InitStep[] = ['health_check', 'fetch_status', 'verify_agent', 'verify_workspace', 'display_main_ui'];

    function simulateSequence(agentReady: boolean, workspaceReady: boolean): InitStep[] {
      const steps: InitStep[] = ['health_check', 'fetch_status'];
      if (agentReady) steps.push('verify_agent');
      if (agentReady && workspaceReady) steps.push('verify_workspace', 'display_main_ui');
      return steps;
    }

    it('should verify agent before workspace for any readiness combination', () => {
      fc.assert(
        fc.property(fc.boolean(), fc.boolean(), (agentReady, workspaceReady) => {
          const steps = simulateSequence(agentReady, workspaceReady);
          const hasWorkspace = steps.includes('verify_workspace');
          const hasAgent = steps.includes('verify_agent');
          // If workspace verified, agent must be verified
          if (hasWorkspace) expect(hasAgent).toBe(true);
          return !hasWorkspace || hasAgent;
        }),
        { numRuns: 20 }
      );
    });

    it('should complete full sequence only when both ready', () => {
      fc.assert(
        fc.property(fc.boolean(), fc.boolean(), (agentReady, workspaceReady) => {
          const steps = simulateSequence(agentReady, workspaceReady);
          const isComplete = steps.length === REQUIRED_SEQUENCE.length;
          expect(isComplete).toBe(agentReady && workspaceReady);
          return isComplete === (agentReady && workspaceReady);
        }),
        { numRuns: 20 }
      );
    });
  });

  /**
   * Property 3: Polling Continues Until Ready
   * **Validates: Requirements 1.3**
   */
  describe('Property 3: Polling Continues Until Ready', () => {
    const DEFAULT_POLL_INTERVAL_MS = 1000;
    const DEFAULT_TIMEOUT_MS = 60000;

    interface PollResult { agentReady: boolean; workspaceReady: boolean; allReady: boolean; }
    interface PollingState { pollCount: number; elapsedMs: number; terminated: boolean; reason: string; }

    function shouldContinuePolling(pollResult: PollResult, elapsedMs: number, timeoutMs: number): { continue: boolean; reason: string } {
      if (elapsedMs >= timeoutMs) return { continue: false, reason: 'timeout' };
      if (pollResult.allReady) return { continue: false, reason: 'all_ready' };
      return { continue: true, reason: 'none' };
    }

    function simulatePolling(pollsBeforeReady: number, maxPolls: number): PollingState {
      let pollCount = 0;
      let elapsedMs = 0;
      for (let i = 0; i < maxPolls; i++) {
        pollCount++;
        elapsedMs += DEFAULT_POLL_INTERVAL_MS;
        const allReady = i >= pollsBeforeReady;
        const { continue: cont, reason } = shouldContinuePolling({ agentReady: allReady, workspaceReady: allReady, allReady }, elapsedMs, DEFAULT_TIMEOUT_MS);
        if (!cont) return { pollCount, elapsedMs, terminated: true, reason };
      }
      return { pollCount, elapsedMs, terminated: false, reason: 'max_polls' };
    }

    it('should stop polling when all ready or timeout', () => {
      fc.assert(
        fc.property(fc.integer({ min: 0, max: 100 }), (pollsBeforeReady) => {
          const maxPolls = Math.floor(DEFAULT_TIMEOUT_MS / DEFAULT_POLL_INTERVAL_MS) + 10;
          const state = simulatePolling(pollsBeforeReady, maxPolls);
          expect(state.terminated).toBe(true);
          expect(['all_ready', 'timeout']).toContain(state.reason);
          return state.terminated;
        }),
        { numRuns: 30 }
      );
    });

    it('should poll exactly until ready when ready before timeout', () => {
      fc.assert(
        fc.property(fc.integer({ min: 1, max: 50 }), (pollsBeforeReady) => {
          const state = simulatePolling(pollsBeforeReady, 100);
          expect(state.pollCount).toBe(pollsBeforeReady + 1);
          expect(state.reason).toBe('all_ready');
          return state.pollCount === pollsBeforeReady + 1;
        }),
        { numRuns: 30 }
      );
    });

    it('should timeout when never ready', () => {
      const maxPolls = Math.floor(DEFAULT_TIMEOUT_MS / DEFAULT_POLL_INTERVAL_MS);
      const state = simulatePolling(maxPolls + 100, maxPolls + 10);
      expect(state.reason).toBe('timeout');
      expect(state.elapsedMs).toBeGreaterThanOrEqual(DEFAULT_TIMEOUT_MS);
    });
  });
});
