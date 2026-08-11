/**
 * Tests for planDispatchBackfill — the pure ToDo-dispatch backfill decision,
 * reused by BOTH the ChatPage bind effect (active-tab path) and the new
 * onTabSessionBound callback (background-tab path). run_2c89bc8d.
 */
import { describe, it, expect } from 'vitest';
import { planDispatchBackfill, type DispatchPendingRecord } from '../dispatchBackfill';

const rec = (tabId: string, todoId: string): DispatchPendingRecord => ({
  tabId, todoId, tabLabel: `Tab-${tabId}`,
});

describe('planDispatchBackfill', () => {
  it('backfills the pending record for a tab that just got its session (the background-tab fix)', () => {
    const records = [rec('t2', 'todoB')];
    const plan = planDispatchBackfill(records, 't2', 'S2');
    expect(plan.toDispatch).toEqual({ tabId: 't2', todoId: 'todoB', tabLabel: 'Tab-t2', sessionId: 'S2' });
    expect(plan.remaining).toEqual([]); // this tab's record pruned
  });

  it('NEWEST-WINS when a tab was dispatched to twice before its session materialized', () => {
    const records = [rec('t2', 'old'), rec('t2', 'newest')];
    const plan = planDispatchBackfill(records, 't2', 'S2');
    expect(plan.toDispatch?.todoId).toBe('newest');
    expect(plan.remaining).toEqual([]); // BOTH of this tab's records dropped
  });

  it('leaves OTHER tabs\' records untouched (per-tab isolation)', () => {
    const records = [rec('t1', 'todoA'), rec('t2', 'todoB')];
    const plan = planDispatchBackfill(records, 't2', 'S2');
    expect(plan.toDispatch?.todoId).toBe('todoB');
    expect(plan.remaining).toEqual([rec('t1', 'todoA')]); // t1 preserved
  });

  it('no-op when the tab has no pending record', () => {
    const records = [rec('t1', 'todoA')];
    const plan = planDispatchBackfill(records, 't2', 'S2');
    expect(plan.toDispatch).toBeNull();
    expect(plan.remaining).toEqual([rec('t1', 'todoA')]);
  });

  it('no-op on missing sessionId or tabId (nothing to bind)', () => {
    const records = [rec('t2', 'todoB')];
    expect(planDispatchBackfill(records, 't2', undefined).toDispatch).toBeNull();
    expect(planDispatchBackfill(records, undefined, 'S2').toDispatch).toBeNull();
  });

  it('empty records → no-op', () => {
    const plan = planDispatchBackfill([], 't2', 'S2');
    expect(plan.toDispatch).toBeNull();
    expect(plan.remaining).toEqual([]);
  });
});
