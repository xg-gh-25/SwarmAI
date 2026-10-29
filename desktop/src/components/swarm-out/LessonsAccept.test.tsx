/** LessonsAccept: a Hive run's lessons wait for the person; taking them sends them to the judge. */
import { describe, it, expect, vi, afterEach, beforeAll, afterAll } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react';

const acceptLessons = vi.fn();
vi.mock('../../services/swarmOut', async () => {
  const actual = await vi.importActual<typeof import('../../services/swarmOut')>('../../services/swarmOut');
  return { ...actual, swarmOutService: { acceptLessons: (...a: unknown[]) => acceptLessons(...a) } };
});
import { LessonsAccept } from './LessonsAccept';
import i18n from '../../i18n';

beforeAll(async () => { await i18n.changeLanguage('zh'); });
afterAll(async () => { await i18n.changeLanguage('en'); });
afterEach(() => { cleanup(); vi.clearAllMocks(); });

const L = (o = {}) => ({ returned: 2, pending: 2, accepted: false, sent: 0, written: 0, rejected: 0, judging: 0,
  skipped: 0, refused: 0, truncated: false, error: null, digests: ['a'.repeat(64), 'b'.repeat(64)], ...o });

describe('LessonsAccept', () => {
  it('shows nothing when nothing is waiting', () => {
    render(<LessonsAccept dispatchId="so_1" lessons={L({ pending: 0 })} />);
    expect(screen.queryByTestId('swarm-out-lessons-accept')).toBeNull();
    render(<LessonsAccept dispatchId="so_1" lessons={null} />);
    expect(screen.queryByTestId('swarm-out-lessons-accept')).toBeNull();
  });

  it('sends the waiting lessons to the judge once taken', async () => {
    acceptLessons.mockResolvedValue(L({ pending: 0, sent: 2, judging: 2, accepted: true }));
    const done = vi.fn();
    render(<LessonsAccept dispatchId="so_1" lessons={L()} onDone={done} />);
    fireEvent.click(screen.getByTestId('swarm-out-lessons-accept'));
    await waitFor(() => expect(screen.getByTestId('swarm-out-lessons-accepted')).toBeTruthy());
    expect(acceptLessons).toHaveBeenCalledWith('so_1', ['a'.repeat(64), 'b'.repeat(64)]);   // what was shown
    expect(done).toHaveBeenCalled();
  });

  it('offers the button again when more lessons arrive', async () => {
    acceptLessons.mockResolvedValue(L({ pending: 0, sent: 2 }));
    const { rerender } = render(<LessonsAccept dispatchId="so_1" lessons={L()} />);
    fireEvent.click(screen.getByTestId('swarm-out-lessons-accept'));
    await waitFor(() => expect(screen.getByTestId('swarm-out-lessons-accepted')).toBeTruthy());
    rerender(<LessonsAccept dispatchId="so_1" lessons={L({ pending: 0, sent: 2 })} />);
    expect(screen.getByTestId('swarm-out-lessons-accepted')).toBeTruthy();
    rerender(<LessonsAccept dispatchId="so_1" lessons={L({ pending: 1, sent: 2 })} />);
    await waitFor(() => expect(screen.getByTestId('swarm-out-lessons-accept')).toBeTruthy());
  });

  it('says why when the host refuses', async () => {
    acceptLessons.mockRejectedValue({ detail: "this run's result has not been taken back yet" });
    render(<LessonsAccept dispatchId="so_1" lessons={L()} />);
    fireEvent.click(screen.getByTestId('swarm-out-lessons-accept'));
    await waitFor(() => expect(screen.getByTestId('swarm-out-lessons-error').textContent).toContain('taken back'));
  });

  it('keeps the button when nothing was queued or some still wait', async () => {
    acceptLessons.mockResolvedValue(L({ error: 'not_queued' }));
    const { unmount } = render(<LessonsAccept dispatchId="so_1" lessons={L()} />);
    fireEvent.click(screen.getByTestId('swarm-out-lessons-accept'));
    await waitFor(() => expect(screen.getByTestId('swarm-out-lessons-error').textContent).toContain('没交给判官'));
    expect(screen.queryByTestId('swarm-out-lessons-accepted')).toBeNull();
    expect(screen.getByTestId('swarm-out-lessons-accept')).toBeTruthy();
    unmount();
    acceptLessons.mockResolvedValue(L({ pending: 1, sent: 1 }));
    render(<LessonsAccept dispatchId="so_1" lessons={L()} />);
    fireEvent.click(screen.getByTestId('swarm-out-lessons-accept'));
    await waitFor(() => expect(acceptLessons).toHaveBeenCalledTimes(2));
    await waitFor(() => expect((screen.getByTestId('swarm-out-lessons-accept') as HTMLButtonElement).disabled).toBe(false));
    expect(screen.queryByTestId('swarm-out-lessons-accepted')).toBeNull();
  });
});
