/**
 * Layer-4 CROSS-BOUNDARY E2E (run_9e42c066) — the swarm:file-changed SEAM.
 *
 * cross_boundary=true (event-bus + ACT/SENSE). Layers 1-3 exercise ONE side each
 * (the hook alone, the rail alone with a fixture). This drives the REAL seam
 * end-to-end: the actual swarm:file-changed window event → the RESIDENT
 * useReferencedFiles listener inside useCanvasHost (NOT mocked — the thing under
 * change) → the files it captures → the REAL CanvasOutputRail rendering those rows.
 *
 * This is the AC3 whole-path proof the spec reviewer flagged as missing: a batch
 * that arrives while Canvas is CLOSED is captured by the resident store and, when
 * Canvas opens, the rail renders those exact rows.
 *
 * MUTATION (documented, run manually): gate the resident useReferencedFiles behind
 * `isOpen` in useCanvasHost (simulate the OLD panel-only listener) → this test goes
 * RED (0 rows after a closed-state batch). Verified in-run: gating on isOpen made
 * the resident-capture assertions fail.
 *
 * Only the far leaf is mocked (api.get + useChangeStatus git badges) — the event
 * bus, the useReferencedFiles listener, and the rail are ALL real.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import { useCanvasHost } from '../useCanvasHost';
import { CanvasOutputRail } from '../../components/file-viewer/CanvasOutputRail';

// Far-leaf mocks ONLY (never the seam): the file-resolve API + the git-status badge
// hook. useReferencedFiles (the listener under change) and CanvasOutputRail are REAL.
vi.mock('../../services/api', () => ({
  default: { get: vi.fn().mockResolvedValue({ data: { resolved_path: 'r.md', content: '' } }) },
}));
vi.mock('../../hooks/useChangeStatus', () => ({ useChangeStatus: () => new Map() }));

// A tiny host: mounts useCanvasHost (the RESIDENT owner) and feeds its captured
// referencedFiles into the REAL rail — exactly the ChatPage → FileViewerPanel →
// CanvasOutputRail wiring, minus the panel chrome.
function CanvasSeamHarness({ tabId }: { tabId: string }) {
  const canvas = useCanvasHost({ activeTabId: tabId, sessionId: 's-' + tabId, isStreaming: false });
  return (
    <div>
      <div data-testid="is-open">{String(canvas.isOpen)}</div>
      <div data-testid="output-count">{canvas.outputCount}</div>
      <CanvasOutputRail files={canvas.referencedFiles} />
    </div>
  );
}

function fileChanged(path: string, tabId: string, kind = 'source-final') {
  window.dispatchEvent(
    new CustomEvent('swarm:file-changed', {
      detail: { path, tabId, operation: 'written', relevance: 'deliverable', kind },
    }),
  );
}

describe('Layer-4 E2E — swarm:file-changed → resident store → rail rows (AC1+AC3)', () => {
  beforeEach(() => {
    sessionStorage.clear();
    vi.clearAllMocks();
  });

  it('a source-final batch arriving while CLOSED is captured and RENDERS in the rail', async () => {
    await act(async () => {
      render(<CanvasSeamHarness tabId="A" />);
    });
    // Canvas closed (no file opened, not manuallyOpen).
    expect(screen.getByTestId('is-open').textContent).toBe('false');

    // The real pipeline-finish seam fires while closed.
    await act(async () => {
      fileChanged('backend/alpha.py', 'A');
      fileChanged('backend/beta.py', 'A');
    });

    // Resident store captured both (the count the ChatHeader pill reads)…
    expect(screen.getByTestId('output-count').textContent).toBe('2');
    // …and the REAL rail rendered those exact rows from the resident files prop.
    expect(screen.getByText('alpha.py')).toBeInTheDocument();
    expect(screen.getByText('beta.py')).toBeInTheDocument();
  });

  it('a background-tab (different tabId) write does NOT leak into this tab rail', async () => {
    await act(async () => {
      render(<CanvasSeamHarness tabId="A" />);
    });
    await act(async () => {
      fileChanged('other/leak.py', 'B'); // stamped for a different tab
    });
    expect(screen.getByTestId('output-count').textContent).toBe('0');
    expect(screen.queryByText('leak.py')).toBeNull();
  });
});
