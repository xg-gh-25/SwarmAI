/**
 * ToDoOverlay interaction tests (run_7ccfe39f — flat-table redesign).
 *
 * Locks the behaviors the todoTable pure-unit tests can't reach — the wired
 * component contract:
 *   1. New ToDo create MUST post workspace_id='swarmws' (else backend 422s).
 *   2. Clicking a row ACTION (Dispatch/Withdraw) must NOT open the detail drawer.
 *   3. Clicking the row BODY DOES open the drawer.
 *   4. Withdraw calls todosService.delete + removes the row optimistically.
 *   5. Status chips filter the table (default 'Open' hides terminal rows).
 *   6. Clicking a column header sorts (toggles direction).
 *   7. Load-error classification (4xx client vs 5xx/network outage).
 *
 * Data source: ToDoContent fetches list() + history() and merges. Both mocked.
 */
import { StrictMode } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { ToDoContent } from '../ToDoOverlay';
import { todosService } from '../../../services/todos';
import { ApiError } from '../../../services/api';
import type { ToDo } from '../../../types/todo';

vi.mock('../../../services/todos', () => ({
  todosService: {
    list: vi.fn(),
    history: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
    dispatch: vi.fn(),
    listAttachments: vi.fn(),
    uploadAttachment: vi.fn(),
    deleteAttachment: vi.fn(),
  },
}));

const RECENT = new Date().toISOString(); // within any range window

function mkTodo(over: Partial<ToDo> = {}): ToDo {
  return {
    id: 't1', workspaceId: 'swarmws', title: 'Seeded todo', description: 'desc',
    source: null, sourceType: 'ai_detected', status: 'pending', priority: 'high',
    dueDate: null,
    linkedContext: JSON.stringify({ next_step: 'do the thing', files: ['a.ts'] }),
    taskId: null, reviewState: null, reviewKind: null,
    dispatchedSessionId: null, dispatchedTabLabel: null, dispatchedAt: null,
    completedAt: null, reviewedAt: null,
    createdAt: RECENT, updatedAt: RECENT,
    ...over,
  };
}

const mockList = (rows: ToDo[]) => (todosService.list as ReturnType<typeof vi.fn>).mockResolvedValue(rows);
const mockHistory = (rows: ToDo[] = []) => (todosService.history as ReturnType<typeof vi.fn>).mockResolvedValue({ todos: rows, count: rows.length });

describe('ToDoOverlay (flat table)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockList([mkTodo()]);
    mockHistory([]);
    (todosService.create as ReturnType<typeof vi.fn>).mockResolvedValue(mkTodo({ id: 'new' }));
    (todosService.delete as ReturnType<typeof vi.fn>).mockResolvedValue(undefined);
    (todosService.listAttachments as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    (todosService.uploadAttachment as ReturnType<typeof vi.fn>).mockResolvedValue({ id: 'att1', todoId: 't1', filename: 'x', relPath: 'x', size: 1, createdAt: RECENT });
    (todosService.deleteAttachment as ReturnType<typeof vi.fn>).mockResolvedValue(undefined);
  });

  it('renders a row for each todo', async () => {
    render(<ToDoContent onDispatch={() => true} close={() => {}} />);
    await screen.findByTestId('todo-overlay');
    expect(await screen.findByTestId('todo-row')).toBeTruthy();
  });

  it('New ToDo posts workspace_id=swarmws + encodes next_step', async () => {
    render(<ToDoContent onDispatch={() => true} close={() => {}} />);
    await screen.findByTestId('todo-overlay');
    fireEvent.click(screen.getByTestId('todo-new-btn'));
    fireEvent.change(await screen.findByTestId('todo-form-title'), { target: { value: 'My task' } });
    fireEvent.change(screen.getByTestId('todo-form-nextstep'), { target: { value: 'Read X first' } });
    fireEvent.click(screen.getByTestId('todo-form-submit'));

    await waitFor(() => expect(todosService.create).toHaveBeenCalledTimes(1));
    const arg = (todosService.create as ReturnType<typeof vi.fn>).mock.calls[0][0];
    expect(arg.workspaceId).toBe('swarmws');
    expect(arg.title).toBe('My task');
    expect(arg.sourceType).toBe('manual');
    expect(JSON.parse(arg.linkedContext).next_step).toBe('Read X first');
  });

  it('New ToDo blocks empty title (no create call)', async () => {
    render(<ToDoContent onDispatch={() => true} close={() => {}} />);
    await screen.findByTestId('todo-overlay');
    fireEvent.click(screen.getByTestId('todo-new-btn'));
    fireEvent.click(await screen.findByTestId('todo-form-submit'));
    expect(await screen.findByTestId('todo-form-err')).toBeTruthy();
    expect(todosService.create).not.toHaveBeenCalled();
  });

  it('clicking a row ACTION does NOT open the drawer (stopPropagation)', async () => {
    render(<ToDoContent onDispatch={() => false} close={() => {}} />);
    await screen.findByTestId('todo-overlay');
    const row = await screen.findByTestId('todo-row');
    fireEvent.click(within(row).getByTestId('todo-action-dispatch'));
    expect(screen.queryByTestId('todo-detail-drawer')).toBeNull();
  });

  it('clicking the row BODY opens the detail drawer with work packet', async () => {
    render(<ToDoContent onDispatch={() => false} close={() => {}} />);
    await screen.findByTestId('todo-overlay');
    const row = await screen.findByTestId('todo-row');
    fireEvent.click(row);
    const drawer = await screen.findByTestId('todo-detail-drawer');
    expect(within(drawer).getByText('do the thing')).toBeTruthy();
  });

  it('Withdraw calls delete and removes the row optimistically', async () => {
    render(<ToDoContent onDispatch={() => false} close={() => {}} />);
    await screen.findByTestId('todo-overlay');
    const row = await screen.findByTestId('todo-row');
    fireEvent.click(within(row).getByTestId('todo-action-withdraw'));
    await waitFor(() => expect(todosService.delete).toHaveBeenCalledWith('t1'));
    // optimistic removal → row gone
    await waitFor(() => expect(screen.queryByTestId('todo-row')).toBeNull());
  });

  it('status chip default Open hides a terminal (completed) todo', async () => {
    mockList([
      mkTodo({ id: 'open1', status: 'pending' }),
      mkTodo({ id: 'done1', status: 'handled', title: 'Done one' }),
    ]);
    render(<ToDoContent onDispatch={() => true} close={() => {}} />);
    await screen.findByTestId('todo-overlay');
    await waitFor(() => expect(screen.getAllByTestId('todo-row').length).toBe(1)); // only the open one
    // switch to All → both visible
    fireEvent.click(screen.getByTestId('todo-chip-All'));
    await waitFor(() => expect(screen.getAllByTestId('todo-row').length).toBe(2));
    // switch to Completed → only the done one
    fireEvent.click(screen.getByTestId('todo-chip-Completed'));
    await waitFor(() => expect(screen.getAllByTestId('todo-row').length).toBe(1));
  });

  it('clicking a column header toggles sort direction (+ shows arrow)', async () => {
    mockList([
      mkTodo({ id: 'a', title: 'Apple', createdAt: RECENT }),
      mkTodo({ id: 'z', title: 'Zebra', createdAt: RECENT }),
    ]);
    // Render inside StrictMode (as main.tsx does) — a nested-setState updater is
    // double-invoked here, which is exactly how the Gate-2 HIGH toggle-no-op bug
    // manifests. Rendering without StrictMode would make this test vacuous.
    render(<StrictMode><ToDoContent onDispatch={() => true} close={() => {}} /></StrictMode>);
    await screen.findByTestId('todo-overlay');
    await waitFor(() => expect(screen.getAllByTestId('todo-row').length).toBe(2));
    const titleTh = screen.getByTestId('todo-th-title');
    fireEvent.click(titleTh); // title asc
    let rows = screen.getAllByTestId('todo-row');
    expect(within(rows[0]).getByText('Apple')).toBeTruthy();
    expect(titleTh.textContent).toContain('▲'); // asc arrow on active header
    fireEvent.click(titleTh); // title desc — the toggle MUST fire (Gate-2 HIGH: nested-setState bug)
    rows = screen.getAllByTestId('todo-row');
    expect(within(rows[0]).getByText('Zebra')).toBeTruthy();
    expect(titleTh.textContent).toContain('▼'); // desc arrow after toggle
  });

  it('changing the time range refetches history with the new window + re-filters', async () => {
    // A todo created 60 days ago: in the 90d window, out of the 7d window.
    const old = new Date(Date.now() - 60 * 24 * 60 * 60 * 1000).toISOString();
    mockList([mkTodo({ id: 'old', title: 'Old todo', createdAt: old, updatedAt: old })]);
    render(<ToDoContent onDispatch={() => true} close={() => {}} />);
    await screen.findByTestId('todo-overlay');
    // default 30d → the 60d-old todo is filtered OUT of the table
    await waitFor(() => expect(screen.queryByTestId('todo-row')).toBeNull());
    // switch to 90d → history refetched with window=90, todo now in range
    fireEvent.click(screen.getByTestId('todo-range-90d'));
    await waitFor(() => expect(todosService.history).toHaveBeenCalledWith(1000, 90));
    await waitFor(() => expect(screen.getAllByTestId('todo-row').length).toBe(1));
    // switch to 7d → filtered out again
    fireEvent.click(screen.getByTestId('todo-range-7d'));
    await waitFor(() => expect(screen.queryByTestId('todo-row')).toBeNull());
  });

  it('Withdraw failure undoes the optimistic removal (row restored via refresh)', async () => {
    mockList([mkTodo({ id: 'keep', title: 'Keep me' })]);
    (todosService.delete as ReturnType<typeof vi.fn>).mockRejectedValueOnce(new Error('network'));
    render(<ToDoContent onDispatch={() => false} close={() => {}} />);
    await screen.findByTestId('todo-overlay');
    const row = await screen.findByTestId('todo-row');
    fireEvent.click(within(row).getByTestId('todo-action-withdraw'));
    // delete rejects → error surfaces + refresh() re-fetches (list still returns the row) → row restored
    expect(await screen.findByTestId('todo-action-error')).toBeTruthy();
    await waitFor(() => expect(screen.getByTestId('todo-row')).toBeTruthy());
  });

  it('shows a truncation hint when a fetch hits the 1000-row cap', async () => {
    const many = Array.from({ length: 1000 }, (_, i) => mkTodo({ id: `t${i}` }));
    mockList(many);
    render(<ToDoContent onDispatch={() => true} close={() => {}} />);
    await screen.findByTestId('todo-overlay');
    expect(await screen.findByTestId('todo-truncated')).toBeTruthy();
  });

  it('does NOT show truncation hint under the cap', async () => {
    mockList([mkTodo()]);
    render(<ToDoContent onDispatch={() => true} close={() => {}} />);
    await screen.findByTestId('todo-overlay');
    await screen.findByTestId('todo-row');
    expect(screen.queryByTestId('todo-truncated')).toBeNull();
  });

  it('renders all 7 sortable column headers', async () => {
    render(<ToDoContent onDispatch={() => true} close={() => {}} />);
    await screen.findByTestId('todo-overlay');
    for (const k of ['priority', 'title', 'source', 'status', 'created', 'updated', 'completed']) {
      expect(screen.getByTestId(`todo-th-${k}`)).toBeTruthy();
    }
  });

  it('KPI row + analytics strip are always rendered', async () => {
    render(<ToDoContent onDispatch={() => true} close={() => {}} />);
    await screen.findByTestId('todo-overlay');
    expect(await screen.findByTestId('todo-kpis')).toBeTruthy();
    expect(screen.getByTestId('todo-analytics')).toBeTruthy();
    expect(screen.getByTestId('todo-weekly')).toBeTruthy();
    expect(screen.getByTestId('todo-sources')).toBeTruthy();
  });

  it('guide banner is present', async () => {
    render(<ToDoContent onDispatch={() => true} close={() => {}} />);
    await screen.findByTestId('todo-overlay');
    expect(screen.getByTestId('todo-guide-banner')).toBeTruthy();
  });

  it('Flow load 4xx is labeled a CLIENT error, NOT a backend outage', async () => {
    (todosService.list as ReturnType<typeof vi.fn>).mockRejectedValue(
      new ApiError({ code: 'VALIDATION_FAILED', message: 'bad limit' }, 400),
    );
    render(<ToDoContent onDispatch={() => true} close={() => {}} />);
    const err = await screen.findByTestId('todo-load-error');
    expect(err.textContent).toContain('HTTP 400');
    expect(err.textContent).toContain('client error');
    expect(err.textContent).not.toContain('backend may be unavailable');
  });

  it('Flow load true outage (5xx) KEEPS the backend-unavailable message', async () => {
    (todosService.list as ReturnType<typeof vi.fn>).mockRejectedValue(
      new ApiError({ code: 'SERVICE_UNAVAILABLE', message: 'down' }, 503),
    );
    render(<ToDoContent onDispatch={() => true} close={() => {}} />);
    const err = await screen.findByTestId('todo-load-error');
    expect(err.textContent).toContain('backend may be unavailable');
    expect(err.textContent).not.toContain('client error');
  });

  it('Flow load non-ApiError (unexpected throw) defaults to unavailable', async () => {
    (todosService.list as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('boom'));
    render(<ToDoContent onDispatch={() => true} close={() => {}} />);
    const err = await screen.findByTestId('todo-load-error');
    expect(err.textContent).toContain('backend may be unavailable');
    expect(err.textContent).not.toContain('client error');
  });

  // Gate-1 F6 safety property: editing a system todo (rich work-packet) must
  // PRESERVE every dispatch key the form doesn't expose. A wholesale-overwrite
  // regression (linkedContext = {next_step} only) fails this — the mutation guard.
  it('editing a work-packet todo preserves files/commits, only changes next_step', async () => {
    const rich = mkTodo({
      id: 't1', sourceType: 'ai_detected',
      linkedContext: JSON.stringify({
        next_step: 'old step', files: ['a.ts', 'b.ts'], commits: ['abc123'],
        design_docs: ['d.md'], sessions: ['s1'], memory_refs: ['m1'],
        blockers: ['x'], acceptance: 'done when green', notes: 'keep me',
      }),
    });
    mockList([rich]);
    (todosService.update as ReturnType<typeof vi.fn>).mockResolvedValue(rich);
    render(<ToDoContent onDispatch={() => false} close={() => {}} />);
    await screen.findByTestId('todo-overlay');
    // open detail → edit
    fireEvent.click(await screen.findByTestId('todo-row'));
    fireEvent.click(await screen.findByTestId('todo-drawer-edit'));
    const nsField = await screen.findByTestId('todo-form-nextstep');
    fireEvent.change(nsField, { target: { value: 'new step' } });
    fireEvent.click(screen.getByTestId('todo-form-submit'));

    await waitFor(() => expect(todosService.update).toHaveBeenCalledTimes(1));
    const [, payload] = (todosService.update as ReturnType<typeof vi.fn>).mock.calls[0];
    const merged = JSON.parse(payload.linkedContext);
    // the ONE changed field
    expect(merged.next_step).toBe('new step');
    // ALL other dispatch keys survive (this is the whole point — no context loss)
    expect(merged.files).toEqual(['a.ts', 'b.ts']);
    expect(merged.commits).toEqual(['abc123']);
    expect(merged.design_docs).toEqual(['d.md']);
    expect(merged.sessions).toEqual(['s1']);
    expect(merged.memory_refs).toEqual(['m1']);
    expect(merged.blockers).toEqual(['x']);
    expect(merged.acceptance).toBe('done when green');
    expect(merged.notes).toBe('keep me');
  });

  // Gate-2 MED#2: clearing the SOLE packet field must PERSIST the clear (send "",
  // not undefined — undefined is skipped by toSnakeCase → the clear silently
  // wouldn't stick and the old value would remain).
  it('clearing the only next_step persists an empty packet (not a silent no-op)', async () => {
    const one = mkTodo({ id: 't1', linkedContext: JSON.stringify({ next_step: 'old step' }) });
    mockList([one]);
    (todosService.update as ReturnType<typeof vi.fn>).mockResolvedValue(one);
    render(<ToDoContent onDispatch={() => false} close={() => {}} />);
    await screen.findByTestId('todo-overlay');
    fireEvent.click(await screen.findByTestId('todo-row'));
    fireEvent.click(await screen.findByTestId('todo-drawer-edit'));
    fireEvent.change(await screen.findByTestId('todo-form-nextstep'), { target: { value: '' } });
    fireEvent.click(screen.getByTestId('todo-form-submit'));

    await waitFor(() => expect(todosService.update).toHaveBeenCalledTimes(1));
    const [, payload] = (todosService.update as ReturnType<typeof vi.fn>).mock.calls[0];
    // linkedContext MUST be present (empty string), not undefined — else the
    // clear is dropped by toSnakeCase and never persisted.
    expect(payload.linkedContext).toBe('');
  });
});
