/**
 * Tests for LibraryHealth — the Native-store health + cleanup section.
 *
 * The load-bearing contract: REVERSIBLE actions (archive) run in ONE click with
 * confirm=false; DESTRUCTIVE actions (delete) NEVER POST with confirm=true until
 * the user passes an explicit inline confirm. Plus the quiet-clean + hidden-on-
 * error discipline (a health widget must not shout on a healthy store).
 *
 * api is mocked at the boundary; the component invents no data.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, act, cleanup, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../../services/api', () => ({
  default: { get: vi.fn(), post: vi.fn() },
}));
import api from '../../services/api';
import { LibraryHealth } from './LibraryHealth';

const ARCHIVE_FINDING = {
  kind: 'archive_old_logs', title: '60 old raw-logs (>90d)', detail: 'move to Archives/',
  action_label: 'Archive to Archives/', actionable: true, reversible: true,
  count: 60, total_bytes: 1000, paths: ['DailyActivity/a.md', 'Signals/b.md'],
};
const DELETE_FINDING = {
  kind: 'delete_empty', title: '37 empty/tiny files (<100B)', detail: 'delete',
  action_label: 'Delete', actionable: true, reversible: false,
  count: 37, total_bytes: 0, paths: ['Notes/empty.md'],
};

function mockHealth(report: unknown) {
  (api.get as ReturnType<typeof vi.fn>).mockResolvedValue({ data: report });
  (api.post as ReturnType<typeof vi.fn>).mockResolvedValue({ data: { status: 'success', applied: 1 } });
}

function renderHealth() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><LibraryHealth /></QueryClientProvider>);
}

beforeEach(() => { vi.clearAllMocks(); });
afterEach(() => { cleanup(); });

describe('LibraryHealth', () => {
  it('renders findings with actions when the store has cleanup candidates', async () => {
    mockHealth({ generated_at: 1, root: 'Knowledge/', clean: false, findings: [ARCHIVE_FINDING, DELETE_FINDING] });
    renderHealth();
    expect(await screen.findByTestId('library-health')).toBeInTheDocument();
    expect(screen.getByText('60 old raw-logs (>90d)')).toBeInTheDocument();
    expect(screen.getByText('37 empty/tiny files (<100B)')).toBeInTheDocument();
  });

  it('ARCHIVE runs in one click with confirm=false (reversible)', async () => {
    mockHealth({ generated_at: 1, root: 'Knowledge/', clean: false, findings: [ARCHIVE_FINDING] });
    renderHealth();
    const btn = await screen.findByTestId('library-health-action-archive_old_logs');
    act(() => { btn.click(); });
    await waitFor(() => expect(api.post as ReturnType<typeof vi.fn>).toHaveBeenCalled());
    const [url, body] = (api.post as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe('/library/health/action');
    expect(body).toMatchObject({ kind: 'archive_old_logs', confirm: false });
  });

  it('DELETE does NOT post on first click — it asks to confirm first (destructive gate)', async () => {
    mockHealth({ generated_at: 1, root: 'Knowledge/', clean: false, findings: [DELETE_FINDING] });
    renderHealth();
    const btn = await screen.findByTestId('library-health-action-delete_empty');
    act(() => { btn.click(); });
    // first click flips to confirm — NO destructive POST yet
    expect(api.post as ReturnType<typeof vi.fn>).not.toHaveBeenCalled();
    expect(await screen.findByTestId('library-health-confirm-delete_empty')).toBeInTheDocument();
    // confirming posts with confirm=true
    act(() => { screen.getByTestId('library-health-confirm-delete_empty').click(); });
    await waitFor(() => expect(api.post as ReturnType<typeof vi.fn>).toHaveBeenCalled());
    const [, body] = (api.post as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(body).toMatchObject({ kind: 'delete_empty', confirm: true });
  });

  it('cancel aborts the delete without posting', async () => {
    mockHealth({ generated_at: 1, root: 'Knowledge/', clean: false, findings: [DELETE_FINDING] });
    renderHealth();
    const btn = await screen.findByTestId('library-health-action-delete_empty');
    act(() => { btn.click(); });
    act(() => { screen.getByTestId('library-health-cancel-delete_empty').click(); });
    await waitFor(() => expect(screen.queryByTestId('library-health-confirm-delete_empty')).toBeNull());
    expect(api.post as ReturnType<typeof vi.fn>).not.toHaveBeenCalled();
  });

  it('shows a quiet "healthy" line (not a void) when the store is clean', async () => {
    mockHealth({ generated_at: 1, root: 'Knowledge/', clean: true, findings: [] });
    renderHealth();
    expect(await screen.findByTestId('library-health-clean')).toBeInTheDocument();
  });

  it('oversized_category is informational — no action button', async () => {
    const OVERSIZED = { kind: 'oversized_category', title: 'DailyActivity is large (45.0M)', detail: 'review',
      action_label: '', actionable: false, reversible: false, count: 1, total_bytes: 45000000, paths: ['DailyActivity'] };
    mockHealth({ generated_at: 1, root: 'Knowledge/', clean: false, findings: [OVERSIZED] });
    renderHealth();
    expect(await screen.findByTestId('library-health-oversized_category')).toBeInTheDocument();
    expect(screen.queryByTestId('library-health-action-oversized_category')).toBeNull();
  });
});
