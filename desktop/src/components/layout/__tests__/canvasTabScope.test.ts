/**
 * Tests for shouldResetCanvasOnTabChange — the Canvas tab-scope reset guard.
 *
 * Canvas is an extension of the CURRENT chat tab, so a real tab switch must clear
 * the opened file + pin/mute/collapsed. The guard's subtlety (Gate-2 flagged the
 * two hard cases): it must NOT clear on the first-set (undefined→value at mount)
 * nor on a same-tab republish (value→same, e.g. when sessionId resolves on a new
 * tab's first message). Signal is tabId (stable per tab), never sessionId.
 */
import { describe, it, expect } from 'vitest';
import { shouldResetCanvasOnTabChange } from '../ThreeColumnLayout';

describe('shouldResetCanvasOnTabChange', () => {
  it('does NOT reset on first-set (mount: undefined → first tab)', () => {
    // App open with a just-opened file must not be nuked.
    expect(shouldResetCanvasOnTabChange(undefined, 'tab-A')).toBe(false);
  });

  it('RESETS on a real switch between two defined tabs', () => {
    expect(shouldResetCanvasOnTabChange('tab-A', 'tab-B')).toBe(true);
  });

  it('does NOT reset on same-tab republish (value → same value)', () => {
    // New tab's session resolving (sessionId undefined→value) republishes meta
    // with the SAME tabId — the file must survive mid-first-message.
    expect(shouldResetCanvasOnTabChange('tab-A', 'tab-A')).toBe(false);
  });

  it('does NOT reset when both undefined (no active tab yet)', () => {
    expect(shouldResetCanvasOnTabChange(undefined, undefined)).toBe(false);
  });

  it('resets when switching away then the next distinct tab is defined', () => {
    // Sequence sanity: A→B→A each distinct transition resets.
    expect(shouldResetCanvasOnTabChange('tab-B', 'tab-A')).toBe(true);
  });

  it('treats value → undefined as a change (defensive; last-tab-close never hits this in practice)', () => {
    // useUnifiedTabState auto-creates a default tab so activeTabId is never
    // undefined post-mount, but the predicate is honest about the transition.
    expect(shouldResetCanvasOnTabChange('tab-A', undefined)).toBe(true);
  });
});
