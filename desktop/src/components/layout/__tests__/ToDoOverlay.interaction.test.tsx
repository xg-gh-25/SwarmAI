/**
 * ToDoOverlay interaction tests (Gate-2 A3) — lock the two mutation-fragile
 * behaviors the parseWorkPacket/todoZones unit tests can't reach:
 *   1. New ToDo create MUST post workspace_id='swarmws' (C1 — else backend 422s).
 *   2. Clicking a card's action button must NOT open the detail drawer
 *      (stopPropagation on the action container).
 *   3. Clicking the card body (not an action) DOES open the drawer.
 *
 * The overlay renders inside a Modal gated on `swarm:show-todo`; we fire that
 * event to open it. todosService is mocked so we assert on the posted payload.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { ToDoOverlay } from '../ToDoOverlay';
import { todosService } from '../../../services/todos';
import type { ToDo } from '../../../types/todo';

vi.mock('../../../services/todos', () => ({
  todosService: {
    list: vi.fn(),
    create: vi.fn(),
    retreat: vi.fn(),
    review: vi.fn(),
    history: vi.fn(),
    historyStats: vi.fn(),
  },
}));

function mkTodo(over: Partial<ToDo> = {}): ToDo {
  return {
    id: 't1', workspaceId: 'swarmws', title: 'Seeded todo', description: 'desc',
    source: null, sourceType: 'ai_detected', status: 'pending', priority: 'high',
    dueDate: null,
    linkedContext: JSON.stringify({ next_step: 'do the thing', files: ['a.ts'] }),
    taskId: null, reviewState: null, reviewKind: null,
    dispatchedSessionId: null, dispatchedTabLabel: null, dispatchedAt: null,
    completedAt: null, reviewedAt: null,
    createdAt: '2026-08-01T00:00:00+00:00', updatedAt: '2026-08-01T00:00:00+00:00',
    ...over,
  };
}

function openOverlay() {
  window.dispatchEvent(new CustomEvent('swarm:show-todo'));
}

describe('ToDoOverlay interactions', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (todosService.list as ReturnType<typeof vi.fn>).mockResolvedValue([mkTodo()]);
    (todosService.create as ReturnType<typeof vi.fn>).mockResolvedValue(mkTodo({ id: 'new' }));
  });

  it('New ToDo posts workspace_id=swarmws + encodes next_step (C1)', async () => {
    render(<ToDoOverlay onDispatch={() => true} />);
    openOverlay();
    await screen.findByTestId('todo-overlay');

    fireEvent.click(screen.getByTestId('todo-new-btn'));
    fireEvent.change(await screen.findByTestId('todo-new-title'), { target: { value: 'My task' } });
    fireEvent.change(screen.getByTestId('todo-new-nextstep'), { target: { value: 'Read X first' } });
    fireEvent.click(screen.getByTestId('todo-new-submit'));

    await waitFor(() => expect(todosService.create).toHaveBeenCalledTimes(1));
    const arg = (todosService.create as ReturnType<typeof vi.fn>).mock.calls[0][0];
    expect(arg.workspaceId).toBe('swarmws');           // C1: required or 422
    expect(arg.title).toBe('My task');
    expect(arg.sourceType).toBe('manual');
    expect(JSON.parse(arg.linkedContext).next_step).toBe('Read X first');
  });

  it('New ToDo blocks empty title (no create call)', async () => {
    render(<ToDoOverlay onDispatch={() => true} />);
    openOverlay();
    await screen.findByTestId('todo-overlay');
    fireEvent.click(screen.getByTestId('todo-new-btn'));
    fireEvent.click(await screen.findByTestId('todo-new-submit'));
    expect(await screen.findByTestId('todo-new-err')).toBeTruthy();
    expect(todosService.create).not.toHaveBeenCalled();
  });

  it('clicking a card ACTION does NOT open the drawer (stopPropagation)', async () => {
    render(<ToDoOverlay onDispatch={() => false} />);
    openOverlay();
    await screen.findByTestId('todo-overlay');
    // seeded todo is in ①To Do → has a Dispatch action
    const card = await screen.findByTestId('todo-card');
    fireEvent.click(within(card).getByTestId('todo-action-dispatch'));
    // drawer must NOT have opened
    expect(screen.queryByTestId('todo-detail-drawer')).toBeNull();
  });

  it('guide banner is persistent in BOTH Flow and History views', async () => {
    (todosService.history as ReturnType<typeof vi.fn>).mockResolvedValue({ todos: [], count: 0 });
    (todosService.historyStats as ReturnType<typeof vi.fn>).mockResolvedValue({
      throughputWeekly: [], completionRate: 0, sourceDistribution: {},
      confirmVsAuto: { manual: 0, auto: 0 }, rejectRate: 0,
      totals: { created: 0, completed: 0, confirmed: 0, rejected: 0, reviewed: 0 },
    });
    render(<ToDoOverlay onDispatch={() => true} />);
    openOverlay();
    await screen.findByTestId('todo-overlay');
    // Flow view (default): banner present
    expect(screen.getByTestId('todo-guide-banner')).toBeTruthy();
    // switch to History: banner still present (not empty-state-gated)
    fireEvent.click(screen.getByTestId('todo-view-history'));
    await screen.findByTestId('todo-history-pane');
    expect(screen.getByTestId('todo-guide-banner')).toBeTruthy();
  });

  it('clicking the card BODY opens the detail drawer', async () => {
    render(<ToDoOverlay onDispatch={() => false} />);
    openOverlay();
    await screen.findByTestId('todo-overlay');
    const card = await screen.findByTestId('todo-card');
    fireEvent.click(card);
    const drawer = await screen.findByTestId('todo-detail-drawer');
    expect(drawer).toBeTruthy();
    // work-packet material rendered
    expect(within(drawer).getByText('do the thing')).toBeTruthy();
  });
});
