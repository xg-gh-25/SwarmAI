/**
 * Onboarding gate predicate tests (AC1).
 *
 * Invariant: a user who has NOT completed onboarding must reach the wizard,
 * regardless of `initialized`. A partial-init new user must never fall through
 * to the (unusable) ChatPage. Returning users (onboardingComplete=true) must
 * NEVER re-see the wizard.
 */
import { describe, it, expect } from 'vitest';
import { shouldShowOnboarding, routeDecision } from '../App';
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

describe('routeDecision — no new-user ChatPage flash', () => {
  it('isLoading → render nothing (never flash ChatPage)', () => {
    expect(routeDecision(undefined, true)).toBe('loading');
  });

  it('status undefined but not flagged loading → still render nothing', () => {
    expect(routeDecision(undefined, false)).toBe('loading');
  });

  it('resolved + not onboarded → onboarding', () => {
    expect(routeDecision(status({ initialized: false, onboardingComplete: false }), false)).toBe('onboarding');
  });

  it('resolved + onboarded → app (ChatPage)', () => {
    expect(routeDecision(status({ onboardingComplete: true }), false)).toBe('app');
  });
});

describe('routeDecision — error state (no blank-screen dead-end)', () => {
  it('query errored + not loading + no status → error (not a permanent blank)', () => {
    // The bug: retry:2 exhausts → status=undefined, isLoading=false, isError=true.
    // Without the error branch this returned 'loading' → AppRoutes rendered null forever.
    expect(routeDecision(undefined, false, true)).toBe('error');
  });

  it('isError defaults to false → existing 2-arg callers unchanged (backward-compat)', () => {
    // The existing call site routeDecision(status, isLoading) must behave identically.
    expect(routeDecision(undefined, false)).toBe('loading');
    expect(routeDecision(status({ onboardingComplete: true }), false)).toBe('app');
  });

  it('still loading wins over error → no error-card flash during retries', () => {
    // react-query keeps isError=false until retries exhaust, but guard anyway:
    // loading must take precedence so a mid-retry tick never flashes the error card.
    expect(routeDecision(undefined, true, true)).toBe('loading');
  });

  it('error ignored once status resolved successfully → app/onboarding, not error', () => {
    // A stale isError alongside a resolved status must not trap the user on the card.
    expect(routeDecision(status({ onboardingComplete: true }), false, true)).toBe('app');
  });
});
