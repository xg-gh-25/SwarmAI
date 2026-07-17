/**
 * Onboarding gate predicate tests (AC1).
 *
 * Invariant: a user who has NOT completed onboarding must reach the wizard,
 * regardless of `initialized`. A partial-init new user must never fall through
 * to the (unusable) ChatPage. Returning users (onboardingComplete=true) must
 * NEVER re-see the wizard.
 */
import { describe, it, expect } from 'vitest';
import { shouldShowOnboarding } from '../App';
import type { SystemStatus } from '../services/system';

function status(partial: Partial<SystemStatus>): SystemStatus {
  return {
    database: { healthy: true },
    agent: { ready: true },
    swarmWorkspace: { ready: true },
    initialized: true,
    onboardingComplete: false,
    ...partial,
  } as SystemStatus;
}

describe('shouldShowOnboarding — gate predicate', () => {
  it('AC1: partial-init NEW user (initialized=false, onboardingComplete=false) → wizard, not ChatPage', () => {
    expect(shouldShowOnboarding(status({ initialized: false, onboardingComplete: false }))).toBe(true);
  });

  it('fully-ready new user (initialized=true, onboardingComplete=false) → wizard', () => {
    expect(shouldShowOnboarding(status({ initialized: true, onboardingComplete: false }))).toBe(true);
  });

  it('returning user (onboardingComplete=true) → NEVER wizard, even if initialized=false', () => {
    expect(shouldShowOnboarding(status({ initialized: false, onboardingComplete: true }))).toBe(false);
    expect(shouldShowOnboarding(status({ initialized: true, onboardingComplete: true }))).toBe(false);
  });

  it('status still loading (undefined) → no wizard (wait for status)', () => {
    expect(shouldShowOnboarding(undefined)).toBe(false);
  });
});
