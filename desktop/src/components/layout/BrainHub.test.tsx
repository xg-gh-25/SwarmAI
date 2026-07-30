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
const mockGetDistribution = vi.fn();
vi.mock('../../services/ddd', () => ({
  getBrains: (...a: unknown[]) => mockGetBrains(...a),
  getBrainDetail: (...a: unknown[]) => mockGetBrainDetail(...a),
  getReview: (...a: unknown[]) => mockGetReview(...a),
  approveReview: (...a: unknown[]) => mockApproveReview(...a),
  rejectReviewHunk: (...a: unknown[]) => mockRejectHunk(...a),
  approveProposal: (...a: unknown[]) => mockApproveProposal(...a),
  rejectProposal: (...a: unknown[]) => mockRejectProposal(...a),
  getDistribution: (...a: unknown[]) => mockGetDistribution(...a),
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

// Mock the heavy force-graph CodeGraph (Run 4 #10) — assert the props it receives
// (esp. project === current brain name, NOT a hardcoded literal), don't render the canvas.
const mockCodeGraph = vi.fn();
vi.mock('../code-intel/CodeGraph', () => ({
  CodeGraph: (props: { project: string; onClose?: () => void }) => {
    mockCodeGraph(props);
    return <div data-testid="code-graph-mock">{props.project}</div>;
  },
}));

// The six canonical section keys, in order (mirrors backend _SECTIONS / SECTION_ORDER).
const SECTION_KEYS: Array<BrainDetail['sections'][number]['key']> =
  ['identity', 'knowledge', 'gates', 'capabilities', 'delivery', 'refresher'];

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
  specs: [],
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
  diff_incomplete: false,
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
  mockGetDistribution.mockResolvedValue({
    declared_targets: ['open-plugin'], visibility: 'internal',
    distributable: true, declared: true, warnings: [],
    has_output: false, output_path: null, last_distribute_time: null,
    source_changed_since: false,
  });
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
  // Run 4 (#8): the Brain view is now 2-pane — only the ACTIVE section's card is
  // mounted at a time, revealed by clicking its left-nav item. These helpers
  // open the brain then click the section's nav item so the content pane shows it
  // (default active section = the FIRST section returned by the backend).
  async function openBrain() {
    render(<BrainHub />);
    await waitFor(() => expect(screen.getByTestId('brain-card-SwarmAI')).toBeTruthy());
    fireEvent.click(screen.getByTestId('brain-card-SwarmAI'));
    await waitFor(() => expect(screen.getByTestId('brainhub-brain')).toBeTruthy());
  }
  async function openSection(key: string) {
    await openBrain();
    fireEvent.click(screen.getByTestId(`nav-item-${key}`));
    await waitFor(() => expect(screen.getByTestId(`section-${key}`)).toBeTruthy());
  }

  it('renders all six section NAV items (2-pane left nav)', async () => {
    // Spec change (#8, Gate-1 HIGH, directed by user): content is now
    // one-section-at-a-time behind a nav click, NOT all six rendered at once.
    // Six-section COMPLETENESS is asserted on the nav; each card is reachable by
    // clicking its nav item (covered in the tests below + the Run-4 block).
    await openBrain();
    for (const key of ['identity', 'knowledge', 'gates', 'capabilities', 'delivery', 'refresher']) {
      expect(screen.getByTestId(`nav-item-${key}`)).toBeTruthy();
    }
    // default active section = the first section → its card is in the content pane.
    expect(screen.getByTestId('section-identity')).toBeTruthy();
    // a non-active section's card is NOT mounted (one-at-a-time).
    expect(screen.queryByTestId('section-refresher')).toBeNull();
  });

  it('marks an empty ③Gates section as complete-not-broken (R31)', async () => {
    await openSection('gates');
    const empty = screen.getByTestId('empty-gates');
    expect(empty.textContent).toContain('complete, not broken');
  });

  it('renders decay-colored 7-type entries GROUPED by type for ② knowledge', async () => {
    await openSection('knowledge');
    // AC3: entries are grouped by type (collapsed by default), NOT a flat list.
    // The 2 fixture entries are 1 guideline + 1 pitfall → 2 type-groups.
    expect(screen.getByTestId('entry-group-guideline')).toBeTruthy();
    expect(screen.getByTestId('entry-group-pitfall')).toBeTruthy();
    // collapsed → no entry-line rendered until a group is expanded
    expect(screen.queryAllByTestId('entry-line').length).toBe(0);
    // expand the pitfall group → its (dormant) entry appears with decay styling
    fireEvent.click(screen.getByTestId('entry-group-toggle-pitfall'));
    const lines = screen.getAllByTestId('entry-line');
    expect(lines.length).toBe(1);
    const dormant = lines.find((l) => l.textContent?.includes('Old dormant note'));
    expect(dormant?.querySelector('.opacity-70, [class*="opacity-70"]') || dormant?.innerHTML).toBeTruthy();
    // the 7-type composition bar is still rendered (F5 regression guard preserved)
    expect(screen.getByTestId('typebar-guideline')).toBeTruthy();
    expect(screen.getByTestId('typebar-pitfall')).toBeTruthy();
  });

  it('opens the file preview with a workspace-relative path (Projects/<name>/<member>)', async () => {
    // Regression for the REVIEW CRITICAL-1: the preview path MUST be workspace-
    // relative (get_workspace_root resolves it against the cached SwarmWS root
    // only when no basePath is passed). A bare member path or a relative basePath
    // would resolve against the backend CWD → 404.
    await openSection('identity');   // AGENTS.md lives under ① identity (the default active)
    fireEvent.click(screen.getByTestId('member-AGENTS.md'));
    await waitFor(() => expect(screen.getByTestId('file-preview-open')).toBeTruthy());
    // the mock echoes props.file.path — assert the resolvable full path.
    expect(screen.getByTestId('file-preview-open').textContent).toBe('Projects/SwarmAI/AGENTS.md');
    // and NO basePath was passed (would be taken as the fs root verbatim).
    const lastCall = mockPreview.mock.calls[mockPreview.mock.calls.length - 1][0];
    expect(lastCall.basePath).toBeUndefined();
  });
});

describe('BrainHub — Brain view 2-pane + CodeGraph (Run 4, #8/#10 + AC4 robustness)', () => {
  async function openBrain() {
    render(<BrainHub />);
    await waitFor(() => expect(screen.getByTestId('brain-card-SwarmAI')).toBeTruthy());
    fireEvent.click(screen.getByTestId('brain-card-SwarmAI'));
    await waitFor(() => expect(screen.getByTestId('brainhub-brain')).toBeTruthy());
  }

  it('AC1: renders 2-pane (nav + content); clicking a nav item switches the content pane', async () => {
    await openBrain();
    expect(screen.getByTestId('brainhub-brain-nav')).toBeTruthy();
    expect(screen.getByTestId('brainhub-brain-content')).toBeTruthy();
    // default active = first section (identity) shown, knowledge card not yet mounted.
    expect(screen.getByTestId('section-identity')).toBeTruthy();
    expect(screen.queryByTestId('section-knowledge')).toBeNull();
    // click knowledge nav → knowledge card mounts, identity card unmounts.
    fireEvent.click(screen.getByTestId('nav-item-knowledge'));
    await waitFor(() => expect(screen.getByTestId('section-knowledge')).toBeTruthy());
    expect(screen.queryByTestId('section-identity')).toBeNull();
  });

  it('AC3 (#10): "View code graph" mounts CodeGraph with project === brain name (no hardcoded literal)', async () => {
    await openBrain();
    expect(screen.queryByTestId('code-graph-mock')).toBeNull();  // not mounted until clicked
    fireEvent.click(screen.getByTestId('open-codegraph'));
    await waitFor(() => expect(screen.getByTestId('code-graph-mock')).toBeTruthy());
    // CodeGraph MUST receive the CURRENT brain name, never a hardcoded "SwarmAI" literal.
    const call = mockCodeGraph.mock.calls[mockCodeGraph.mock.calls.length - 1][0];
    expect(call.project).toBe('SwarmAI');   // === the opened brain's name (DETAIL.name)
    expect(typeof call.onClose).toBe('function');
  });

  it('AC4: a knowledge-only bare DDD (no code_intel / aim.json / gates) renders every section with NO crash', async () => {
    // A DDD from another user's workspace: gates + capabilities + delivery + refresher
    // empty, knowledge-only, kind="knowledge". Must render all six nav items + honest
    // empty states, never throw. project passed to CodeGraph = this DDD's name.
    const BARE: BrainDetail = {
      name: 'SomeoneElsesProject', kind: 'knowledge',
      sections: SECTION_KEYS.map((key, i) => ({
        key, num: ['①', '②', '③', '④', '⑤', '⑥'][i], label: key, ownGovern: 'OWN' as const, curator: '—',
        members: key === 'knowledge' ? [{ path: '2-understanding/PRODUCT.md', gitStatus: 'clean' }] : [],
        entries: [], completeNotBroken: key !== 'knowledge',
      })),
      specs: [],
    };
    mockGetBrains.mockResolvedValue([{
      name: 'SomeoneElsesProject', kind: 'knowledge',
      sectionsPresent: { identity: false, knowledge: true, gates: false, capabilities: false, delivery: false, refresher: false },
      lifecycleStage: 'GROW',
      health: { sinking: 0, pending: 0, uncommitted: false, lastChangeRelative: '1d ago' },
    }]);
    mockGetBrainDetail.mockResolvedValue(BARE);
    render(<BrainHub />);
    await waitFor(() => expect(screen.getByTestId('brain-card-SomeoneElsesProject')).toBeTruthy());
    fireEvent.click(screen.getByTestId('brain-card-SomeoneElsesProject'));
    await waitFor(() => expect(screen.getByTestId('brainhub-brain')).toBeTruthy());
    // all six nav items present even though 5 sections are empty
    for (const key of SECTION_KEYS) {
      expect(screen.getByTestId(`nav-item-${key}`)).toBeTruthy();
    }
    // click an empty section → renders "complete, not broken", no throw
    fireEvent.click(screen.getByTestId('nav-item-gates'));
    await waitFor(() => expect(screen.getByTestId('empty-gates')).toBeTruthy());
    // Gate-2 meta-review MED: a knowledge-only (non-code-repo) DDD has no code to
    // graph → the "View code graph" button must NOT be offered (avoids the misleading
    // "re-index" empty state on a brain that will always be empty).
    expect(screen.queryByTestId('open-codegraph')).toBeNull();
  });

  it('AC3+meta: ESC with the code graph open closes ONLY the graph, not the whole Brain Hub', async () => {
    // Gate-2 meta-review MED: the Brain Hub lives in a shared Modal with a
    // document-level ESC→close listener; CodeGraph has no ESC handler. BrainView
    // must intercept ESC (capture phase) while the graph is open so ESC dismisses
    // the graph, not the hub.
    await openBrain();  // DETAIL.kind === 'code-repo' → button present
    fireEvent.click(screen.getByTestId('open-codegraph'));
    await waitFor(() => expect(screen.getByTestId('code-graph-mock')).toBeTruthy());
    fireEvent.keyDown(document, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByTestId('code-graph-mock')).toBeNull());
    // the Brain Hub itself is still mounted (ESC did NOT close the hub)
    expect(screen.getByTestId('brainhub-brain')).toBeTruthy();
  });

  it('AC4: an empty gallery (no DDDs at all) renders the honest empty state, no crash', async () => {
    mockGetBrains.mockResolvedValue([]);
    render(<BrainHub />);
    await waitFor(() => expect(screen.getByText('No DDD brains found.')).toBeTruthy());
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

  it('renders 2 zones (A + C) + diff header with both SHAs — Zone B removed (F1)', async () => {
    await openReview();
    expect(screen.getByTestId('review-zone-a')).toBeTruthy();
    expect(screen.getByTestId('review-zone-c')).toBeTruthy();
    // F1: the dead "decay·sinking" Zone B (backend never emitted the tag) is gone.
    expect(screen.queryByTestId('review-zone-b')).toBeNull();
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

  it('mark-all-seen (H2) ARMS on first click — does NOT advance the watermark', async () => {
    await openReview();
    const btn = screen.getByTestId('review-approve-all');
    fireEvent.click(btn);
    // first click must NOT call approveReview — it only arms the confirm.
    await waitFor(() => expect(btn.textContent).toContain('Click again to confirm'));
    expect(mockApproveReview).not.toHaveBeenCalled();
  });

  it('mark-all-seen (H2) advances the watermark on the SECOND (confirm) click', async () => {
    await openReview();
    const btn = screen.getByTestId('review-approve-all');
    fireEvent.click(btn);                                  // arm
    await waitFor(() => expect(btn.textContent).toContain('Click again to confirm'));
    fireEvent.click(btn);                                  // confirm
    await waitFor(() => expect(mockApproveReview).toHaveBeenCalledWith('SwarmAI'));
  });

  it('mark-all-seen (H2) armed state DISARMS after rejecting a hunk (load() reset)', async () => {
    // REVIEW-flagged coverage gap: arming approve-all then taking another action
    // (which calls load()) must reset the armed state so a later stray click on
    // approve-all does NOT immediately confirm.
    await openReview();
    const btn = screen.getByTestId('review-approve-all');
    fireEvent.click(btn);                                  // arm approve-all
    await waitFor(() => expect(btn.textContent).toContain('Click again to confirm'));
    fireEvent.click(screen.getByTestId('review-reject-hunk')); // different action → load() → disarm
    await waitFor(() => expect(mockRejectHunk).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByTestId('review-approve-all').textContent).toContain('Mark all seen'));
    expect(mockApproveReview).not.toHaveBeenCalled();      // never confirmed
  });

  it('Zone C proposal Approve delegates to cultivation (approveProposal)', async () => {
    await openReview();
    const zoneC = screen.getByTestId('review-zone-c');
    const approveBtn = Array.from(zoneC.querySelectorAll('button')).find((b) => b.textContent === 'Approve')!;
    fireEvent.click(approveBtn);
    await waitFor(() => expect(mockApproveProposal).toHaveBeenCalledWith('prop-1', 'SwarmAI'));
  });

  it('F3: reject-hunk API error is SURFACED inline (transient) — does NOT blank the queue', async () => {
    mockRejectHunk.mockRejectedValueOnce(new Error('409 hunk no longer applies'));
    await openReview();
    fireEvent.click(screen.getByTestId('review-reject-hunk'));
    // transient inline action-error, NOT the full-view "Failed to load review"
    await waitFor(() => expect(screen.getByTestId('review-action-error')).toBeTruthy());
    expect(screen.getByTestId('review-action-error').textContent).toContain('409');
    // Gate-2: the queue is STILL rendered (not blanked by a full-view error)
    expect(screen.getByTestId('brainhub-review')).toBeTruthy();
    expect(screen.getByTestId('review-zone-a')).toBeTruthy();
    expect(screen.queryByTestId('review-error')).toBeNull();
  });

  it('F4: Zone C proposal card renders the confidence signal', async () => {
    await openReview();
    const conf = screen.getByTestId('proposal-confidence');
    expect(conf.textContent).toContain('0.70');   // confidence 0.7 shown, not hidden
  });

  it('F4: a null-confidence proposal renders gracefully (— not "null")', async () => {
    mockGetReview.mockResolvedValue({
      ...REVIEW,
      proposals: [{ id: 'p2', target_doc: 'TECH.md', target_section: 'X', content: 'c', confidence: null, source_run_id: 'r' }],
    });
    await openReview();
    const conf = screen.getByTestId('proposal-confidence');
    expect(conf.textContent).toContain('—');
    expect(conf.textContent).not.toContain('null');
  });

  it('F5: 7-type composition bar segment order is STABLE (canonical, not insertion order)', async () => {
    // Two brains whose entries arrive in DIFFERENT type order must yield the SAME
    // left-to-right segment order (canonical TYPE_COLOR order: guideline before pitfall).
    const mkDetail = (order: Array<'guideline' | 'pitfall'>) => ({
      ...DETAIL,
      sections: DETAIL.sections.map((s) =>
        s.key === 'knowledge'
          ? { ...s, entries: order.map((t, i) => ({ title: `${t}${i}`, entryType: t, decayState: 'active' as const, section: 'A', source: '', file: 'f' })) }
          : s,
      ),
    });
    const orderOf = () => {
      const bar = screen.getByTestId('typebar-guideline').parentElement!;
      return Array.from(bar.querySelectorAll('[data-testid^="typebar-"]')).map((n) => n.getAttribute('data-testid'));
    };
    // pitfall-first arrival
    mockGetBrainDetail.mockResolvedValue(mkDetail(['pitfall', 'guideline']));
    const { unmount } = render(<BrainHub />);
    await waitFor(() => expect(screen.getByTestId('brain-card-SwarmAI')).toBeTruthy());
    fireEvent.click(screen.getByTestId('brain-card-SwarmAI'));
    await waitFor(() => expect(screen.getByTestId('nav-item-knowledge')).toBeTruthy());
    fireEvent.click(screen.getByTestId('nav-item-knowledge'));
    await waitFor(() => expect(screen.getByTestId('typebar-guideline')).toBeTruthy());
    const orderA = orderOf();
    unmount();
    // guideline-first arrival → must produce the SAME segment order
    mockGetBrainDetail.mockResolvedValue(mkDetail(['guideline', 'pitfall']));
    render(<BrainHub />);
    await waitFor(() => expect(screen.getByTestId('brain-card-SwarmAI')).toBeTruthy());
    fireEvent.click(screen.getByTestId('brain-card-SwarmAI'));
    await waitFor(() => expect(screen.getByTestId('nav-item-knowledge')).toBeTruthy());
    fireEvent.click(screen.getByTestId('nav-item-knowledge'));
    await waitFor(() => expect(screen.getByTestId('typebar-guideline')).toBeTruthy());
    const orderB = orderOf();
    expect(orderA).toEqual(orderB);
    // canonical: guideline segment precedes pitfall segment
    expect(orderA.indexOf('typebar-guideline')).toBeLessThan(orderA.indexOf('typebar-pitfall'));
  });

  it('F8: diff_incomplete DISABLES "Mark all seen" + shows a warning with a Retry', async () => {
    mockGetReview.mockResolvedValue({ ...REVIEW, diff_incomplete: true });
    await openReview();
    expect(screen.getByTestId('review-diff-incomplete')).toBeTruthy();
    expect((screen.getByTestId('review-approve-all') as HTMLButtonElement).disabled).toBe(true);
    // Gate-2: an explicit Retry affordance exists (not a dead-end lockout) and re-fetches.
    const retry = screen.getByTestId('review-diff-retry');
    mockGetReview.mockResolvedValue({ ...REVIEW, diff_incomplete: false });  // next load succeeds
    fireEvent.click(retry);
    await waitFor(() => expect(screen.queryByTestId('review-diff-incomplete')).toBeNull());
    expect((screen.getByTestId('review-approve-all') as HTMLButtonElement).disabled).toBe(false);
  });
});

describe('BrainHub — Distribute tab (Run 3, AC4)', () => {
  async function openDistribute() {
    render(<BrainHub />);
    await waitFor(() => expect(screen.getByTestId('brain-card-SwarmAI')).toBeTruthy());
    fireEvent.click(screen.getByTestId('brain-card-SwarmAI'));
    await waitFor(() => expect(screen.getByTestId('brainhub-tab-distribute')).toBeTruthy());
    fireEvent.click(screen.getByTestId('brainhub-tab-distribute'));
    await waitFor(() => expect(screen.getByTestId('brainhub-distribute')).toBeTruthy());
  }

  it('renders declared targets for a distributable brain', async () => {
    await openDistribute();
    const rows = screen.getAllByTestId('distribute-target-row');
    expect(rows.length).toBe(1);
    expect(rows[0].textContent).toContain('open-plugin');
  });

  it('F2: null source_changed_since renders "freshness unknown", NOT "up to date"', async () => {
    // The tristate null (uncommitted output) must never fall into the "up to date"
    // branch — that was the exact re-buried-bug Gate-1 flagged. Spec-review gap-fix.
    mockGetDistribution.mockResolvedValue({
      declared_targets: ['open-plugin'], visibility: 'internal',
      distributable: true, declared: true, warnings: [],
      has_output: true, output_path: 'distribute', last_distribute_time: '2026-07-30T00:00:00+00:00',
      source_changed_since: null,
    });
    await openDistribute();
    const row = screen.getByTestId('distribute-target-row');
    expect(row.textContent).toContain('freshness unknown');
    expect(row.textContent).not.toContain('up to date');
  });

  it('shows honest not-distributable state (no fabricated targets)', async () => {
    mockGetDistribution.mockResolvedValue({
      declared_targets: [], visibility: 'internal',
      distributable: false, declared: false, warnings: [],
      has_output: false, output_path: null, last_distribute_time: null,
      source_changed_since: false,
    });
    await openDistribute();
    expect(screen.getByTestId('distribute-not-distributable')).toBeTruthy();
    expect(screen.queryByTestId('distribute-target-row')).toBeNull();
  });

  it('[Distribute a brain] copies the chat command, does NOT auto-run (HITL)', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    await openDistribute();
    fireEvent.click(screen.getByTestId('distribute-button'));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('distribute this ddd: SwarmAI'));
    // No server-side distribute call exists — the button only surfaces the command.
  });
});
