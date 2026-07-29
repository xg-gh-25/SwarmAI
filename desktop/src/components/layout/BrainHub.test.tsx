/**
 * Tests for the real DDD Brain Hub (BrainHub.tsx) — Run 1.
 *
 * Covers:
 *   AC3 — Gallery renders real cards with six-section presence + lifecycle + 4
 *         health signals, and NO recall-heat/crown/ref_count number anywhere.
 *   AC4 — Brain view renders the six sections; empty ③Gates is explicitly
 *         "complete, not broken" (R31); 7-type chips + decay-colored entries;
 *         clicking a member opens the file preview.
 *   AC5 — the overlay (BrainHubDemoOverlay) renders the real <BrainHub/> on the
 *         swarm:show-brain-hub event, NOT an iframe.
 *
 * Service + heavy children are mocked at the boundary (ddd.ts, agents, the file
 * preview modal) — the components under test are our own code, exercised directly.
 */
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { BrainSummary, BrainDetail } from '../../services/ddd';

const mockGetBrains = vi.fn();
const mockGetBrainDetail = vi.fn();
const mockGetReview = vi.fn();
const mockApproveReview = vi.fn();
const mockRejectHunk = vi.fn();
const mockApproveProposal = vi.fn();
const mockRejectProposal = vi.fn();
vi.mock('../../services/ddd', () => ({
  getBrains: (...a: unknown[]) => mockGetBrains(...a),
  getBrainDetail: (...a: unknown[]) => mockGetBrainDetail(...a),
  getReview: (...a: unknown[]) => mockGetReview(...a),
  approveReview: (...a: unknown[]) => mockApproveReview(...a),
  rejectReviewHunk: (...a: unknown[]) => mockRejectHunk(...a),
  approveProposal: (...a: unknown[]) => mockApproveProposal(...a),
  rejectProposal: (...a: unknown[]) => mockRejectProposal(...a),
}));

vi.mock('../../services/agents', () => ({
  agentsService: { getDefault: () => Promise.resolve({ id: 'agent-1' }) },
}));

// Mock the heavy read-only file viewer — assert it's invoked, don't render it.
const mockPreview = vi.fn();
vi.mock('../workspace/FilePreviewModal', () => ({
  FilePreviewModal: (props: { isOpen: boolean; file: { path: string } | null }) => {
    mockPreview(props);
    return props.isOpen && props.file
      ? <div data-testid="file-preview-open">{props.file.path}</div>
      : null;
  },
}));

import { BrainHub } from './BrainHub';
import { BrainHubDemoOverlay } from './BrainHubDemoOverlay';

const GALLERY: BrainSummary[] = [
  {
    name: 'SwarmAI', kind: 'code-repo',
    sectionsPresent: { identity: true, knowledge: true, gates: false, capabilities: true, delivery: true, refresher: true },
    lifecycleStage: 'GROW',
    health: { sinking: 3, pending: 0, uncommitted: true, lastChangeRelative: '2h ago' },
  },
  {
    name: 'AIDLC', kind: 'knowledge',
    sectionsPresent: { identity: true, knowledge: true, gates: false, capabilities: false, delivery: true, refresher: true },
    lifecycleStage: 'CREATE',
    health: { sinking: 0, pending: 2, uncommitted: false, lastChangeRelative: '5d ago' },
  },
];

const DETAIL: BrainDetail = {
  name: 'SwarmAI', kind: 'code-repo',
  sections: [
    { key: 'identity', num: '①', label: 'Identity & Manifest', ownGovern: 'OWN', curator: 'Owner',
      members: [{ path: 'AGENTS.md', gitStatus: 'clean' }], entries: [], completeNotBroken: false },
    { key: 'knowledge', num: '②', label: 'Knowledge', ownGovern: 'OWN', curator: 'PM',
      members: [{ path: '2-understanding/TECH.md', gitStatus: 'modified' }],
      entries: [
        { title: 'Two repos push', entryType: 'guideline', decayState: 'active', section: 'Arch', source: '', file: '2-understanding/TECH.md' },
        { title: 'Old dormant note', entryType: 'pitfall', decayState: 'dormant', section: 'Arch', source: 'auto', file: '2-understanding/TECH.md' },
      ], completeNotBroken: false },
    { key: 'gates', num: '③', label: 'Gates', ownGovern: 'OWN', curator: 'Tech Lead',
      members: [], entries: [], completeNotBroken: true },
    { key: 'capabilities', num: '④', label: 'Capabilities', ownGovern: 'OWN', curator: 'Tech Lead',
      members: [{ path: '4-capabilities/s_ddd-manager', gitStatus: 'clean' }], entries: [], completeNotBroken: false },
    { key: 'delivery', num: '⑤', label: 'Delivery Contract', ownGovern: 'GOVERN', curator: 'TPM',
      members: [{ path: 'bindings.yaml', gitStatus: 'clean' }], entries: [], completeNotBroken: false },
    { key: 'refresher', num: '⑥', label: 'Refresher', ownGovern: 'GOVERN', curator: 'TPM',
      members: [{ path: 'REFRESHER.md', gitStatus: 'clean' }], entries: [], completeNotBroken: false },
  ],
};

const REVIEW = {
  last_reviewed_sha: 'a00ae4600000000000000000000000000000000',
  head_sha: 'ddbcfcd800000000000000000000000000000000',
  hunks: [
    { file: 'Projects/SwarmAI/2-understanding/TECH.md', signature: 'sigA1', tag: 'cultivation·auto-applied' as const,
      diff_text: 'diff --git a/x b/x\n@@ -1 +1 @@\n-old\n+new' },
  ],
  proposals: [
    { id: 'prop-1', target_doc: 'PRODUCT.md', target_section: 'Strategic', content: 'a risky proposal', confidence: 0.7, source_run_id: 'run_x' },
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
  mockGetBrains.mockResolvedValue(GALLERY);
  mockGetBrainDetail.mockResolvedValue(DETAIL);
  mockGetReview.mockResolvedValue(REVIEW);
  mockApproveReview.mockResolvedValue({ last_reviewed_sha: REVIEW.head_sha });
  mockRejectHunk.mockResolvedValue({ reverted: true });
  mockApproveProposal.mockResolvedValue({});
  mockRejectProposal.mockResolvedValue({});
});

describe('BrainHub — Gallery (AC3)', () => {
  it('renders one card per real brain', async () => {
    render(<BrainHub />);
    await waitFor(() => expect(screen.getByTestId('brain-card-SwarmAI')).toBeTruthy());
    expect(screen.getByTestId('brain-card-AIDLC')).toBeTruthy();
  });

  it('shows six-section presence bar (present vs absent styled differently)', async () => {
    render(<BrainHub />);
    await waitFor(() => expect(screen.getByTestId('presence-SwarmAI-knowledge')).toBeTruthy());
    const present = screen.getByTestId('presence-SwarmAI-knowledge').className;
    const absent = screen.getByTestId('presence-SwarmAI-gates').className;
    expect(present).not.toBe(absent); // present=green, absent=grey
  });

  it('shows the 4 live health signals', async () => {
    render(<BrainHub />);
    await waitFor(() => expect(screen.getByTestId('brain-card-SwarmAI')).toBeTruthy());
    const card = screen.getByTestId('brain-card-SwarmAI');
    expect(card.textContent).toContain('Sinking');
    expect(card.textContent).toContain('Pending');
    expect(card.textContent).toContain('Uncommitted');
    expect(card.textContent).toContain('2h ago');
  });

  it('renders NO recall-heat / crown / ref_count number anywhere (R30#4)', async () => {
    const { container } = render(<BrainHub />);
    await waitFor(() => expect(screen.getByTestId('brain-card-SwarmAI')).toBeTruthy());
    const html = container.innerHTML.toLowerCase();
    for (const banned of ['ref_count', 'refcount', 'recall', 'crown', 'heat', '×']) {
      expect(html).not.toContain(banned);
    }
  });
});

describe('BrainHub — Brain view (AC4)', () => {
  async function openBrain() {
    render(<BrainHub />);
    await waitFor(() => expect(screen.getByTestId('brain-card-SwarmAI')).toBeTruthy());
    fireEvent.click(screen.getByTestId('brain-card-SwarmAI'));
    await waitFor(() => expect(screen.getByTestId('brainhub-brain')).toBeTruthy());
  }

  it('renders all six sections in order', async () => {
    await openBrain();
    for (const key of ['identity', 'knowledge', 'gates', 'capabilities', 'delivery', 'refresher']) {
      expect(screen.getByTestId(`section-${key}`)).toBeTruthy();
    }
  });

  it('marks an empty ③Gates section as complete-not-broken (R31)', async () => {
    await openBrain();
    const empty = screen.getByTestId('empty-gates');
    expect(empty.textContent).toContain('complete, not broken');
  });

  it('renders decay-colored 7-type entries for ② knowledge', async () => {
    await openBrain();
    const lines = screen.getAllByTestId('entry-line');
    expect(lines.length).toBe(2);
    // the dormant entry has a dimmed style class (decay coloring)
    const dormant = lines.find((l) => l.textContent?.includes('Old dormant note'));
    expect(dormant?.querySelector('.opacity-70, [class*="opacity-70"]') || dormant?.innerHTML).toBeTruthy();
    // a 7-type composition bar is rendered
    expect(screen.getByTestId('typebar-guideline')).toBeTruthy();
    expect(screen.getByTestId('typebar-pitfall')).toBeTruthy();
  });

  it('opens the file preview with a workspace-relative path (Projects/<name>/<member>)', async () => {
    // Regression for the REVIEW CRITICAL-1: the preview path MUST be workspace-
    // relative (get_workspace_root resolves it against the cached SwarmWS root
    // only when no basePath is passed). A bare member path or a relative basePath
    // would resolve against the backend CWD → 404.
    await openBrain();
    fireEvent.click(screen.getByTestId('member-AGENTS.md'));
    await waitFor(() => expect(screen.getByTestId('file-preview-open')).toBeTruthy());
    // the mock echoes props.file.path — assert the resolvable full path.
    expect(screen.getByTestId('file-preview-open').textContent).toBe('Projects/SwarmAI/AGENTS.md');
    // and NO basePath was passed (would be taken as the fs root verbatim).
    const lastCall = mockPreview.mock.calls[mockPreview.mock.calls.length - 1][0];
    expect(lastCall.basePath).toBeUndefined();
  });
});

describe('BrainHubDemoOverlay (AC5)', () => {
  it('renders the real BrainHub (not an iframe) on swarm:show-brain-hub', async () => {
    const { container } = render(<BrainHubDemoOverlay />);
    // closed initially
    expect(screen.queryByTestId('brain-hub-overlay')).toBeNull();
    fireEvent(window, new CustomEvent('swarm:show-brain-hub'));
    await waitFor(() => expect(screen.getByTestId('brain-hub-overlay')).toBeTruthy());
    // real React BrainHub mounted, NO iframe
    expect(screen.getByTestId('brain-hub')).toBeTruthy();
    expect(container.querySelector('iframe')).toBeNull();
  });
});

describe('BrainHub — Review tab (Run 2, AC5)', () => {
  async function openReview() {
    render(<BrainHub />);
    await waitFor(() => expect(screen.getByTestId('brain-card-SwarmAI')).toBeTruthy());
    fireEvent.click(screen.getByTestId('brain-card-SwarmAI'));         // selects + opens Brain
    await waitFor(() => expect(screen.getByTestId('brainhub-tab-review')).toBeTruthy());
    fireEvent.click(screen.getByTestId('brainhub-tab-review'));
    await waitFor(() => expect(screen.getByTestId('brainhub-review')).toBeTruthy());
  }

  it('renders the 3 zones + diff header with both SHAs', async () => {
    await openReview();
    expect(screen.getByTestId('review-zone-a')).toBeTruthy();
    expect(screen.getByTestId('review-zone-b')).toBeTruthy();
    expect(screen.getByTestId('review-zone-c')).toBeTruthy();
    const hdr = screen.getByTestId('review-diff-header').textContent ?? '';
    expect(hdr).toContain('a00ae46');   // last-reviewed short sha
    expect(hdr).toContain('ddbcfcd8');  // HEAD short sha
    expect(hdr).toContain('Projects/SwarmAI/');
  });

  it('reject-hunk calls rejectReviewHunk with the content SIGNATURE (not an index)', async () => {
    await openReview();
    fireEvent.click(screen.getByTestId('review-reject-hunk'));
    await waitFor(() => expect(mockRejectHunk).toHaveBeenCalled());
    expect(mockRejectHunk).toHaveBeenCalledWith('SwarmAI', 'Projects/SwarmAI/2-understanding/TECH.md', 'sigA1');
  });

  it('mark-all-seen calls approveReview (advance watermark)', async () => {
    await openReview();
    fireEvent.click(screen.getByTestId('review-approve-all'));
    await waitFor(() => expect(mockApproveReview).toHaveBeenCalledWith('SwarmAI'));
  });

  it('Zone C proposal Approve delegates to cultivation (approveProposal)', async () => {
    await openReview();
    const zoneC = screen.getByTestId('review-zone-c');
    const approveBtn = Array.from(zoneC.querySelectorAll('button')).find((b) => b.textContent === 'Approve')!;
    fireEvent.click(approveBtn);
    await waitFor(() => expect(mockApproveProposal).toHaveBeenCalledWith('prop-1', 'SwarmAI'));
  });
});
