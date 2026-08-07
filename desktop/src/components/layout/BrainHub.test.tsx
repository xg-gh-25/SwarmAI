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
const mockGetPinned = vi.fn<[], string[]>(() => []);  // default: no pins → flat-grid fallback
const mockGetBrainDetail = vi.fn();
const mockGetReview = vi.fn();
const mockApproveReview = vi.fn();
const mockRejectHunk = vi.fn();
const mockApproveProposal = vi.fn();
const mockRejectProposal = vi.fn();
const mockGetDistribution = vi.fn();
vi.mock('../../services/ddd', () => ({
  getBrains: (...a: unknown[]) => mockGetBrains(...a),
  // getBrainsWithPinned derives from the same mockGetBrains fixture + a pinned list
  // (mockGetPinned lets a test override; default = SwarmAI first, no others resolvable
  // in the small fixtures → flat-grid fallback, preserving the existing assertions).
  getBrainsWithPinned: async (...a: unknown[]) => ({
    brains: await mockGetBrains(...a),
    pinned: mockGetPinned(),
  }),
  getBrainDetail: (...a: unknown[]) => mockGetBrainDetail(...a),
  getReview: (...a: unknown[]) => mockGetReview(...a),
  approveReview: (...a: unknown[]) => mockApproveReview(...a),
  rejectReviewHunk: (...a: unknown[]) => mockRejectHunk(...a),
  approveProposal: (...a: unknown[]) => mockApproveProposal(...a),
  rejectProposal: (...a: unknown[]) => mockRejectProposal(...a),
  getDistribution: (...a: unknown[]) => mockGetDistribution(...a),
  // pure aggregation helper — real impl (no spy needed; contract-new export, R27)
  aggregateTypeCounts: (sections: { entries: { entryType: string }[] }[]) => {
    const c: Record<string, number> = {
      guideline: 0, pitfall: 0, decision: 0, model: 0, process: 0, principle: 0, correction: 0,
    };
    let total = 0;
    for (const s of sections) for (const e of s.entries) { c[e.entryType] = (c[e.entryType] ?? 0) + 1; total += 1; }
    return total > 0 ? c : undefined;
  },
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

// codeIntel service — CodeGraph (mocked above) is the only consumer now; keep both
// exports mocked so nothing hits the real API.
vi.mock('../../services/codeIntel', () => ({
  getCodeIntelSummary: vi.fn(),
  getCodeIntelGraph: vi.fn(),
}));

// The Brain detail content is now the real Projects/<name> file tree (run_a75197d9).
// Mock LibraryTree at the boundary — assert it's rooted at Projects/<name> and that
// its onFileOpen is wired; the tree's own render is tested in LibraryTree.test.tsx.
const mockLibraryTree = vi.fn();
vi.mock('./LibraryTree', () => ({
  LibraryTree: (props: { rootPath?: string; onFileOpen?: (p: string) => void }) => {
    mockLibraryTree(props);
    return (
      <div data-testid="library-tree-mock" data-rootpath={props.rootPath}>
        <button data-testid="tree-file-click"
          onClick={() => props.onFileOpen?.(`${props.rootPath}/2-understanding/TECH.md`)}>
          open a file
        </button>
      </div>
    );
  },
}));

// The six canonical section keys, in order (mirrors backend _SECTIONS / SECTION_ORDER).
const SECTION_KEYS: Array<BrainDetail['sections'][number]['key']> =
  ['identity', 'knowledge', 'gates', 'capabilities', 'delivery', 'refresher'];

import { BrainHub, hunkSummary } from './BrainHub';

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
  specs: ['channels.spec.md', 'pipeline.spec.md'],
  hasCodeIntel: true,
  health: {
    noise: { reclaimable: 3, rate: 0.12 },
    trust: {
      'TECH.md': { Architecture: 'full', Runtime: 'moderate' },
      'PRODUCT.md': { Vision: 'high', Risks: 'low' },
    },
    escalationPending: 2,
    recall: { value: null, experimental: true },
    diagnostics: {
      'TECH.md': { sections: { Architecture: { composite: 88, trust: 'full' } } },
    },
    computedAt: '2026-08-01T18:27:57Z',
  },
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
  mockGetPinned.mockReturnValue([]);  // default: flat-grid fallback (existing assertions)
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
    await waitFor(() => expect(screen.getByTestId('dddcard-SwarmAI')).toBeTruthy());
    expect(screen.getByTestId('dddcard-AIDLC')).toBeTruthy();
  });

  it('partitions non-pinned brains into a NEEDS-YOU zone (pending>0) above a CALM zone (pending==0)', async () => {
    // flat-grid fallback (no pinned) still splits by pending: AIDLC pending=2 → needs, SwarmAI pending=0 → calm
    render(<BrainHub />);
    await waitFor(() => expect(screen.getByTestId('dddcard-AIDLC')).toBeTruthy());
    const needs = screen.getByTestId('brainhub-needs-zone');
    const calm = screen.getByTestId('brainhub-calm-zone');
    // AIDLC (pending=2) lives in needs; SwarmAI (pending=0) lives in calm
    expect(needs.querySelector('[data-testid="dddcard-AIDLC"]')).toBeTruthy();
    expect(calm.querySelector('[data-testid="dddcard-SwarmAI"]')).toBeTruthy();
    // and NOT vice-versa
    expect(needs.querySelector('[data-testid="dddcard-SwarmAI"]')).toBeNull();
  });

  it('a CALM gallery card no longer shows the redundant-ink widgets (presence / 2×2 cheap grid)', async () => {
    render(<BrainHub />);
    await waitFor(() => expect(screen.getByTestId('dddcard-SwarmAI')).toBeTruthy());
    // SwarmAI is pending==0 → calm card → presence bar + boxed cheap grid are GONE
    expect(screen.queryByTestId('presence-SwarmAI-knowledge')).toBeNull();
    expect(screen.queryByTestId('dddcard-cheap-sinking')).toBeNull();
    // calm card keeps its muted meta line (lifecycle · last-change) and stays clickable
    expect(screen.getByTestId('dddcard-SwarmAI').textContent).toContain('2h ago');
  });

  it('renders NO recall-heat / crown / ref_count number anywhere (R30#4)', async () => {
    const { container } = render(<BrainHub />);
    await waitFor(() => expect(screen.getByTestId('dddcard-SwarmAI')).toBeTruthy());
    const html = container.innerHTML.toLowerCase();
    for (const banned of ['ref_count', 'refcount', 'recall', 'crown', 'heat', '×']) {
      expect(html).not.toContain(banned);
    }
  });

  it('deep-link: swarm:show-brain-hub with detail.brain opens THAT brain (Brain Home calm-click)', async () => {
    render(<BrainHub />);
    await waitFor(() => expect(screen.getByTestId('dddcard-SwarmAI')).toBeTruthy());
    // starts on Gallery
    expect(screen.queryByTestId('brainhub-brain')).toBeNull();
    // fire the deep-link event carrying a target brain
    window.dispatchEvent(new CustomEvent('swarm:show-brain-hub', { detail: { brain: 'AIDLC' } }));
    await waitFor(() => expect(screen.getByTestId('brainhub-brain')).toBeTruthy());
    // routed to the AIDLC brain view (tab label carries the selected name)
    expect(screen.getByTestId('brainhub-tab-brain').textContent).toContain('AIDLC');
  });

  it('deep-link with NO detail.brain leaves Gallery as the default view', async () => {
    render(<BrainHub />);
    await waitFor(() => expect(screen.getByTestId('dddcard-SwarmAI')).toBeTruthy());
    window.dispatchEvent(new CustomEvent('swarm:show-brain-hub'));
    // no target → stays on Gallery, does not jump to a brain view
    expect(screen.queryByTestId('brainhub-brain')).toBeNull();
  });
});


describe('BrainHub — Brain detail = Projects tree + Code Graph toggle (run_a75197d9)', () => {
  async function openBrain() {
    render(<BrainHub />);
    await waitFor(() => expect(screen.getByTestId('dddcard-SwarmAI')).toBeTruthy());
    fireEvent.click(screen.getByTestId('dddcard-SwarmAI'));
    await waitFor(() => expect(screen.getByTestId('brainhub-brain')).toBeTruthy());
  }

  it('detail content is the real Projects/<name> file tree (NOT the old section nav)', async () => {
    await openBrain();
    const tree = await screen.findByTestId('library-tree-mock');
    expect(tree.getAttribute('data-rootpath')).toBe('Projects/SwarmAI');
    expect(screen.queryByTestId('brainhub-brain-nav')).toBeNull();
    expect(screen.queryByTestId('nav-item-identity')).toBeNull();
    expect(screen.queryByTestId('code-intel-panel')).toBeNull();
  });

  it('clicking a tree file closes the overlay THEN opens it in Canvas (workspace-relative, z-index precedent)', async () => {
    const onClose = vi.fn();
    const openFile = vi.fn();
    document.addEventListener('swarm:open-file', openFile as EventListener);
    render(<BrainHub onRequestClose={onClose} />);
    await waitFor(() => expect(screen.getByTestId('dddcard-SwarmAI')).toBeTruthy());
    fireEvent.click(screen.getByTestId('dddcard-SwarmAI'));
    await screen.findByTestId('library-tree-mock');
    fireEvent.click(screen.getByTestId('tree-file-click'));
    expect(onClose).toHaveBeenCalled();
    await waitFor(() => expect(openFile).toHaveBeenCalled());
    const evt = openFile.mock.calls[0][0] as CustomEvent<{ path: string }>;
    expect(evt.detail.path).toBe('Projects/SwarmAI/2-understanding/TECH.md');
    document.removeEventListener('swarm:open-file', openFile as EventListener);
  });

  it('hasCodeIntel → [Files|Code Graph] toggle; Code Graph shows inline CodeGraph, Files shows the tree', async () => {
    await openBrain();
    expect(screen.getByTestId('brainhub-view-toggle')).toBeTruthy();
    expect(screen.getByTestId('library-tree-mock')).toBeTruthy();
    expect(screen.queryByTestId('code-graph-mock')).toBeNull();
    fireEvent.click(screen.getByTestId('view-toggle-graph'));
    await waitFor(() => expect(screen.getByTestId('code-graph-mock')).toBeTruthy());
    expect(mockCodeGraph).toHaveBeenCalledWith(expect.objectContaining({ project: 'SwarmAI', inline: true }));
    expect(screen.queryByTestId('library-tree-mock')).toBeNull();
    fireEvent.click(screen.getByTestId('view-toggle-files'));
    await waitFor(() => expect(screen.getByTestId('library-tree-mock')).toBeTruthy());
    expect(screen.queryByTestId('code-graph-mock')).toBeNull();
  });

  it('a brain with NO code_intel shows the tree only — no toggle, no graph', async () => {
    mockGetBrainDetail.mockResolvedValue({ ...DETAIL, hasCodeIntel: false });
    await openBrain();
    expect(screen.getByTestId('library-tree-mock')).toBeTruthy();
    expect(screen.queryByTestId('brainhub-view-toggle')).toBeNull();
    expect(screen.queryByTestId('code-graph-mock')).toBeNull();
  });

  it('the ontology/needs-you health strip is KEPT above the tree', async () => {
    await openBrain();
    expect(screen.getByTestId('brainhub-healthstrip')).toBeTruthy();
  });
});

describe('BrainHub — Gallery primary hero is clickable (AC6, run_a607f2b0)', () => {
  it('clicking the FULL-density primary hero opens its brain view', async () => {
    // AC6: the pinned primary hero (full DddCard) must open the brain, same as a
    // compact card. Pin SwarmAI so it renders as the full primary (not flat-grid).
    mockGetPinned.mockReturnValue(['SwarmAI']);
    render(<BrainHub />);
    await waitFor(() => expect(screen.getByTestId('brainhub-pinned-row')).toBeTruthy());
    fireEvent.click(screen.getByTestId('dddcard-SwarmAI'));
    await waitFor(() => expect(screen.getByTestId('brainhub-brain')).toBeTruthy());
  });
});


// BrainHubDemoOverlay (AC5) describe removed 2026-08-04 (M5): the legacy overlay
// wrapper was deleted — brain-hub renders through the OverlayHost registry now
// (overlaySurfaces registers `brain-hub` → <BrainHub/>). The "renders the real
// BrainHub, not an iframe" guarantee is covered by the Gallery/Brain-view blocks
// above (they render <BrainHub/> directly) + OverlayHost.test (mount/geometry).

describe('BrainHub — Review tab (Run 2, AC5)', () => {
  async function openReview() {
    render(<BrainHub />);
    await waitFor(() => expect(screen.getByTestId('dddcard-SwarmAI')).toBeTruthy());
    fireEvent.click(screen.getByTestId('dddcard-SwarmAI'));         // selects + opens Brain
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

  // ── Run 2 enrichment (run_32cd6a60) ──────────────────────────────────────────
  it('AC1: a hunk shows a plain-language summary line (file + add/del counts)', async () => {
    // fixture diff_text = 'diff --git a/x b/x\n@@ -1 +1 @@\n-old\n+new' → 1 add, 1 del.
    await openReview();
    const summary = screen.getByTestId('hunk-summary-sigA1');
    // the DDD-doc filename (not the raw a/x b/x header) + the counted change
    expect(summary.textContent).toContain('TECH.md');
    expect(summary.textContent).toContain('+1');
    expect(summary.textContent).toContain('1');   // -1 deletion (the +++/--- headers excluded)
  });

  it('AC2: the raw @@ diff is COLLAPSED by default, revealed by [View diff]', async () => {
    await openReview();
    // collapsed by default → the raw diff <pre> is not shown
    expect(screen.queryByTestId('hunk-diff-sigA1')).toBeNull();
    fireEvent.click(screen.getByTestId('hunk-toggle-diff-sigA1'));
    await waitFor(() => expect(screen.getByTestId('hunk-diff-sigA1')).toBeTruthy());
    // the diff body content is now visible
    expect(screen.getByTestId('hunk-diff-sigA1').textContent).toContain('+new');
  });

  it('AC3/AC4: [Open file] on a hunk closes the overlay THEN opens hunk.file DIRECTLY in Canvas (no double-prefix)', async () => {
    const seq: string[] = [];
    const onRequestClose = vi.fn(() => seq.push('close'));
    const openEvents: CustomEvent[] = [];
    const onOpen = (e: Event) => { seq.push('open'); openEvents.push(e as CustomEvent); };
    document.addEventListener('swarm:open-file', onOpen);
    try {
      render(<BrainHub onRequestClose={onRequestClose} />);
      await waitFor(() => expect(screen.getByTestId('dddcard-SwarmAI')).toBeTruthy());
      fireEvent.click(screen.getByTestId('dddcard-SwarmAI'));
      await waitFor(() => expect(screen.getByTestId('brainhub-tab-review')).toBeTruthy());
      fireEvent.click(screen.getByTestId('brainhub-tab-review'));
      await waitFor(() => expect(screen.getByTestId('brainhub-review')).toBeTruthy());
      fireEvent.click(screen.getByTestId('hunk-open-file-sigA1'));
      await waitFor(() => expect(openEvents.length).toBe(1));
      // hunk.file is ALREADY workspace-relative → dispatched DIRECTLY, never re-wrapped.
      expect(openEvents[0].detail.path).toBe('Projects/SwarmAI/2-understanding/TECH.md');
      // close BEFORE open (z-index precedent, Gate-1)
      expect(seq).toEqual(['close', 'open']);
    } finally {
      document.removeEventListener('swarm:open-file', onOpen);
    }
  });

  it('AC4-neg: proposals have NO Open-file button (target_doc is a bare filename, no resolvable path — Gate-1)', async () => {
    await openReview();
    const zoneC = screen.getByTestId('review-zone-c');
    // Zone C proposal has Approve/Reject but NOT an Open-file affordance
    expect(zoneC.querySelector('[data-testid^="proposal-open-file"]')).toBeNull();
  });
});

describe('hunkSummary — pure helper (run_32cd6a60)', () => {
  it('counts +/- body lines, EXCLUDES +++/--- file headers, parses the @@ line-range', () => {
    const diff = 'diff --git a/x b/x\n--- a/2-understanding/TECH.md\n+++ b/2-understanding/TECH.md\n@@ -10,3 +10,4 @@ Some heading\n context\n-removed\n+added one\n+added two';
    const s = hunkSummary(diff);
    expect(s.adds).toBe(2);      // "+added one", "+added two" — NOT the "+++ b/..." header
    expect(s.dels).toBe(1);      // "-removed" — NOT the "--- a/..." header
    expect(s.startLine).toBe(10);
    expect(s.section).toBe('Some heading');
  });

  it('handles the no-comma single-line header form (@@ -1 +1 @@) without NaN/crash', () => {
    const s = hunkSummary('diff --git a/x b/x\n@@ -1 +1 @@\n-old\n+new');
    expect(s.adds).toBe(1);
    expect(s.dels).toBe(1);
    expect(s.startLine).toBe(1);
    expect(s.section).toBeUndefined();  // empty trailing heading → undefined, never '' or garbage
  });
});

describe('BrainHub — Distribute tab (Run 3, AC4)', () => {
  async function openDistribute() {
    render(<BrainHub />);
    await waitFor(() => expect(screen.getByTestId('dddcard-SwarmAI')).toBeTruthy());
    fireEvent.click(screen.getByTestId('dddcard-SwarmAI'));
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

  // ── Run 3 enrichment (run_037ecbfb) — guided steps + Open-aim.json ────────────
  it('AC2: a distributable brain shows Step 2 (confirm) + Step 3 (run) guided headers', async () => {
    await openDistribute();  // default fixture: distributable, open-plugin
    // distribute-step is a shared get-ALL marker (3 headers reuse it) → getAllByTestId;
    // data-step disambiguates a specific header (avoids the singular-getByTestId trap).
    const steps = screen.getAllByTestId('distribute-step');
    expect(steps.map((s) => s.getAttribute('data-step'))).toEqual(['2', '3']);
    expect(steps.some((s) => (s.textContent ?? '').includes('Step 2'))).toBe(true);
    expect(steps.some((s) => (s.textContent ?? '').includes('Step 3'))).toBe(true);
  });

  it('AC1: a NEVER-declared brain shows Step 1 (declare reach), not a re-declare label', async () => {
    mockGetDistribution.mockResolvedValue({
      declared_targets: [], visibility: 'internal', distributable: false, declared: false,
      warnings: [], has_output: false, output_path: null, last_distribute_time: null, source_changed_since: false,
    });
    await openDistribute();
    const step = screen.getByTestId('distribute-step');
    expect(step.textContent).toContain('Step 1');
    expect(screen.queryByTestId('distribute-redeclare')).toBeNull();  // not the orphaned label
  });

  it('AC5/Gate-1: an ORPHANED brain (has_output && !distributable) is labeled re-declare, NOT "Step 1"', async () => {
    // reach was declared + distributed once, then the block removed → the step header
    // must NOT lie "Step 1 · declare reach" above the orphaned-output warning.
    mockGetDistribution.mockResolvedValue({
      declared_targets: [], visibility: 'internal', distributable: false, declared: false,
      warnings: [], has_output: true, output_path: 'distribute', last_distribute_time: '2026-07-30T00:00:00+00:00',
      source_changed_since: null,
    });
    await openDistribute();
    expect(screen.getByTestId('distribute-redeclare')).toBeTruthy();
    // the honest orphaned warning is still shown, and NO plain "Step 1" header
    expect(screen.getByTestId('distribute-stale-output')).toBeTruthy();
    const step = screen.queryByTestId('distribute-step');
    expect(step?.textContent ?? '').not.toContain('Step 1');
  });

  it('AC3/AC4: [Open aim.json] on the not-declared branch closes overlay THEN opens Projects/<name>/aim.json in Canvas', async () => {
    mockGetDistribution.mockResolvedValue({
      declared_targets: [], visibility: 'internal', distributable: false, declared: false,
      warnings: [], has_output: false, output_path: null, last_distribute_time: null, source_changed_since: false,
    });
    const seq: string[] = [];
    const onRequestClose = vi.fn(() => seq.push('close'));
    const openEvents: CustomEvent[] = [];
    const onOpen = (e: Event) => { seq.push('open'); openEvents.push(e as CustomEvent); };
    document.addEventListener('swarm:open-file', onOpen);
    try {
      render(<BrainHub onRequestClose={onRequestClose} />);
      await waitFor(() => expect(screen.getByTestId('dddcard-SwarmAI')).toBeTruthy());
      fireEvent.click(screen.getByTestId('dddcard-SwarmAI'));
      await waitFor(() => expect(screen.getByTestId('brainhub-tab-distribute')).toBeTruthy());
      fireEvent.click(screen.getByTestId('brainhub-tab-distribute'));
      await waitFor(() => expect(screen.getByTestId('brainhub-distribute')).toBeTruthy());
      fireEvent.click(screen.getByTestId('distribute-open-aim'));
      await waitFor(() => expect(openEvents.length).toBe(1));
      // aim.json is PROJECT-relative → needs the Projects/<name>/ prefix (BrainView shape, NOT hunk.file)
      expect(openEvents[0].detail.path).toBe('Projects/SwarmAI/aim.json');
      expect(seq).toEqual(['close', 'open']);
    } finally {
      document.removeEventListener('swarm:open-file', onOpen);
    }
  });

  it('AC5-simplicity: the DISTRIBUTABLE branch has NO [Open aim.json] button (Gate-1: block already valid, next action is run)', async () => {
    await openDistribute();  // distributable fixture
    expect(screen.queryByTestId('distribute-open-aim')).toBeNull();
  });
});

describe('BrainHub — Detail HealthStrip (design 2026-08-04)', () => {
  async function openBrain() {
    render(<BrainHub />);
    await waitFor(() => expect(screen.getByTestId('dddcard-SwarmAI')).toBeTruthy());
    fireEvent.click(screen.getByTestId('dddcard-SwarmAI'));
    await waitFor(() => expect(screen.getByTestId('brainhub-brain')).toBeTruthy());
  }

  it('renders the redesigned body: verdict + full ontology + needs-you + facts', async () => {
    await openBrain();
    await waitFor(() => expect(screen.getByTestId('brainhub-healthstrip')).toBeTruthy());
    const strip = screen.getByTestId('brainhub-healthstrip');
    expect(strip.querySelector('[data-testid="ddd-verdict"]')).toBeTruthy();
    expect(strip.querySelector('[data-testid="ddd-needs-you"]')).toBeTruthy();
    expect(strip.querySelector('[data-testid="ddd-fact-trust"]')).toBeTruthy();
    expect(strip.querySelector('[data-testid="ddd-fact-activity"]')).toBeTruthy();
  });

  it('verdict dot reads pending only (needs decision when escalations > 0), NOT "healthy"', async () => {
    await openBrain();
    const strip = await screen.findByTestId('brainhub-healthstrip');
    const v = strip.querySelector('[data-testid="ddd-verdict"]');
    // DETAIL fixture has escalationPending=2 → needs decision
    expect(v?.textContent?.toLowerCase()).toMatch(/needs decision|decision/i);
    expect(v?.textContent?.toLowerCase()).not.toContain('healthy');
  });

  it('needs-you lists the reclaimable + escalation actionables', async () => {
    await openBrain();
    const needs = await screen.findByTestId('ddd-needs-you');
    // fixture: escalationPending=2, noise.reclaimable=3
    expect(needs.textContent).toMatch(/2|3/);
    expect(needs.textContent?.toLowerCase()).toMatch(/review|reclaim/);
  });

  it('trust fact is a DISTRIBUTION (% ≥ high), NOT a collapsed rollup verdict word', async () => {
    await openBrain();
    const f = await screen.findByTestId('ddd-fact-trust');
    expect(f.textContent).toMatch(/%|≥ high|not computed/);
    expect(f.textContent).not.toContain('moderate');  // no rollup verdict
  });

  it('diagnostics WALL is deleted (no per-section score dump)', async () => {
    await openBrain();
    await waitFor(() => expect(screen.getByTestId('brainhub-healthstrip')).toBeTruthy());
    expect(screen.queryByTestId('health-diagnostics')).toBeNull();
  });

  it('renders NOTHING when health is undefined (daemon-skew guard)', async () => {
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    const { health: _omit, ...noHealth } = DETAIL;
    mockGetBrainDetail.mockResolvedValueOnce(noHealth);
    render(<BrainHub />);
    await waitFor(() => expect(screen.getByTestId('dddcard-SwarmAI')).toBeTruthy());
    fireEvent.click(screen.getByTestId('dddcard-SwarmAI'));
    await waitFor(() => expect(screen.getByTestId('brainhub-brain')).toBeTruthy());
    expect(screen.queryByTestId('brainhub-healthstrip')).toBeNull();
  });

  it('renders NOTHING (no crash) when health is a PARTIAL object missing noise (O023 boundary, Gate-2 MED)', async () => {
    // A partial/old daemon could send `health` present but the required `noise`
    // field absent — a wire-type violation the runtime must survive, not TypeError.
    mockGetBrainDetail.mockResolvedValueOnce({ ...DETAIL, health: {} as BrainDetail['health'] });
    render(<BrainHub />);
    await waitFor(() => expect(screen.getByTestId('dddcard-SwarmAI')).toBeTruthy());
    fireEvent.click(screen.getByTestId('dddcard-SwarmAI'));
    await waitFor(() => expect(screen.getByTestId('brainhub-brain')).toBeTruthy());
    expect(screen.queryByTestId('brainhub-healthstrip')).toBeNull();
  });
});
