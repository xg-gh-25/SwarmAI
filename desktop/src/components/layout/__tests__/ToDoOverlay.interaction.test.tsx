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
    delete: vi.fn(),
    dispatch: vi.fn(),
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
    fireEvent.change(await screen.findByTestId('todo-new-title'), { target: { value: 'My task' } });
    fireEvent.change(screen.getByTestId('todo-new-nextstep'), { target: { value: 'Read X first' } });
    fireEvent.click(screen.getByTestId('todo-new-submit'));

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
    fireEvent.click(await screen.findByTestId('todo-new-submit'));
    expect(await screen.findByTestId('todo-new-err')).toBeTruthy();
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

  it('clicking a column header toggles sort direction', async () => {
    mockList([
      mkTodo({ id: 'a', title: 'Apple', createdAt: RECENT }),
      mkTodo({ id: 'z', title: 'Zebra', createdAt: RECENT }),
    ]);
    render(<ToDoContent onDispatch={() => true} close={() => {}} />);
    await screen.findByTestId('todo-overlay');
    await waitFor(() => expect(screen.getAllByTestId('todo-row').length).toBe(2));
    const titleTh = screen.getByTestId('todo-th-title');
    fireEvent.click(titleTh); // title asc
    let rows = screen.getAllByTestId('todo-row');
    expect(within(rows[0]).getByText('Apple')).toBeTruthy();
    fireEvent.click(titleTh); // title desc
    rows = screen.getAllByTestId('todo-row');
    expect(within(rows[0]).getByText('Zebra')).toBeTruthy();
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
});
