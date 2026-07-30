/**
 * BrainHub — the real (product) DDD Brain Hub Phase-1 lens, Run 1.
 *
 * Replaces the static demo iframe. A read-only visualization over the live DDD
 * state exposed by GET /api/ddd/brains[/{name}] (which projects ddd_paths +
 * parse_entries + git — no new source of truth, no stored metric). Two internal
 * tab views (the seam for Run2 Review / Run3 Distribute tabs — no router needed):
 *
 *   ① Gallery — one card per DDD brain: six-section presence bar + lifecycle
 *      progress + 4 live health signals (Sinking / Pending / Uncommitted /
 *      Last-change). NO recall-heat/crown number (ref_count is dead — R30#4).
 *   ② Brain   — six-section tree (grouped ①..⑥, each file with curator label +
 *      git-status dot), 7-type chips + decay coloring from real parse_entries,
 *      an empty ③Gates explicitly marked "complete, not broken" (R31). Clicking
 *      a file opens the existing read-only FilePreviewModal.
 *
 * Reuses: FilePreviewModal (read-only file viewer). No new tree/editor built.
 */
import { useCallback, useEffect, useState } from 'react';
import {
  getBrains, getBrainDetail, getReview, approveReview, rejectReviewHunk,
  approveProposal, rejectProposal, getDistribution,
} from '../../services/ddd';
import type {
  BrainSummary, BrainDetail, BrainSection, KnowledgeEntry, EntryType, DecayState, SectionKey,
  ReviewData, ReviewHunk, PendingProposal, DistributionState,
} from '../../services/ddd';
import { agentsService } from '../../services/agents';
import { FilePreviewModal } from '../workspace/FilePreviewModal';
import { CodeGraph } from '../code-intel/CodeGraph';
import { getCodeIntelSummary, type CodeIntelSummary } from '../../services/codeIntel';

// ── Visual constants ──────────────────────────────────────────────────────────

const SECTION_ORDER: SectionKey[] = ['identity', 'knowledge', 'gates', 'capabilities', 'delivery', 'refresher'];

const SECTION_NUM: Record<string, string> = {
  identity: '①', knowledge: '②', gates: '③',
  capabilities: '④', delivery: '⑤', refresher: '⑥',
};

const DECAY_STYLE: Record<DecayState, string> = {
  active: 'text-[#e6edf3]',
  dormant: 'text-[#8b949e] opacity-70',
  archived: 'text-[#5b636d] line-through opacity-50',
};

const TYPE_COLOR: Record<EntryType, string> = {
  guideline: '#3b82f6', pitfall: '#ef4444', decision: '#a855f7',
  model: '#14b8a6', process: '#f59e0b', principle: '#eab308',
  correction: '#ec4899',
};

// Project-relative dir where spec-details/*.spec.md live (mirrors the backend
// SPEC_DETAILS_DIR constant); used to build the file-preview open path.
const SPEC_DETAILS_REL = 'spec-details';

// ── Asset nav keys ──────────────────────────────────────────────────────────
// Specs + Code-Intelligence are ASSET PROJECTIONS, NOT the six canonical DDD
// sections (R31: the SectionKey union + backend _SECTIONS stay untouched). They
// live in the left nav under a divided "Assets" group, selected exactly like a
// section, but keyed on a SEPARATE channel so they can NEVER collide with a real
// SectionKey (the `asset:` prefix guarantees disjointness) and so widening never
// breaks the section-derived currentKey fall-through (Gate-1 F4).
type AssetKey = 'asset:specs' | 'asset:codeintel';
const isAssetKey = (k: string | null): k is AssetKey =>
  k === 'asset:specs' || k === 'asset:codeintel';

const GIT_DOT: Record<string, string> = {
  clean: 'transparent', modified: '#f0a500', added: '#3fb950',
  untracked: '#8b949e', deleted: '#ef4444', renamed: '#a855f7', conflicting: '#ef4444',
};

const LIFECYCLE_STEPS = ['CREATE', 'GROW', 'REVIEW', 'DISTRIBUTE'] as const;

// ── Root ───────────────────────────────────────────────────────────────────────

type Tab = 'gallery' | 'brain' | 'review' | 'distribute';

export function BrainHub() {
  const [tab, setTab] = useState<Tab>('gallery');
  const [brains, setBrains] = useState<BrainSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [agentId, setAgentId] = useState<string>('');

  useEffect(() => {
    let alive = true;
    getBrains().then(
      (b) => alive && setBrains(b),
      (e) => alive && setError(String(e?.message ?? e)),
    );
    agentsService.getDefault().then(
      (a) => alive && setAgentId(a?.id ?? ''),
      () => {/* preview just won't open without an agent — non-fatal */},
    );
    return () => { alive = false; };
  }, []);

  const openBrain = useCallback((name: string) => {
    setSelected(name);
    setTab('brain');
  }, []);

  return (
    <div className="flex flex-col h-full bg-[#0e1117] text-[#e6edf3]" data-testid="brain-hub">
      <div className="flex items-center gap-1 px-3 h-9 border-b border-[#222831] flex-shrink-0 text-[12px]">
        <TabBtn active={tab === 'gallery'} onClick={() => setTab('gallery')} testid="brainhub-tab-gallery">Gallery</TabBtn>
        <TabBtn active={tab === 'brain'} onClick={() => setTab('brain')} disabled={!selected} testid="brainhub-tab-brain">
          Brain{selected ? ` · ${selected}` : ''}
        </TabBtn>
        <TabBtn active={tab === 'review'} onClick={() => setTab('review')} disabled={!selected} testid="brainhub-tab-review">
          Review
        </TabBtn>
        <TabBtn active={tab === 'distribute'} onClick={() => setTab('distribute')} disabled={!selected} testid="brainhub-tab-distribute">
          Distribute
        </TabBtn>
      </div>

      <div className="flex-1 overflow-auto">
        {error && <div className="p-4 text-[#ef4444] text-[13px]" data-testid="brainhub-error">Failed to load brains: {error}</div>}
        {!error && tab === 'gallery' && <Gallery brains={brains} onOpen={openBrain} />}
        {/* key={selected} ties BrainView's identity to the brain — DEFENSIVE (Gate-2
            MED, verified NOT-currently-reachable): today every `selected` change is a
            gallery-card click, and the gallery tab only renders when tab==='gallery',
            so a brain switch is always brain→gallery→brain and THIS conditional already
            unmounts BrainView on the gallery step (fresh mount on return). The key
            guards a FUTURE in-place brain switch (e.g. a "jump to brain" affordance in
            the brain view) from surviving a stale activeKey='asset:codeintel' and
            transiently firing CodeIntelPanel's O(n) fetch for the new brain. Cheap +
            intent-clear; no test asserts it because the transient isn't reachable yet. */}
        {!error && tab === 'brain' && selected && <BrainView key={selected} name={selected} agentId={agentId} />}
        {!error && tab === 'review' && selected && <ReviewView name={selected} />}
        {!error && tab === 'distribute' && selected && <DistributeView name={selected} />}
      </div>
    </div>
  );
}

function TabBtn({ active, disabled, onClick, testid, children }: {
  active: boolean; disabled?: boolean; onClick: () => void; testid: string; children: React.ReactNode;
}) {
  return (
    <button
      data-testid={testid}
      disabled={disabled}
      onClick={onClick}
      className={`px-3 py-1 rounded-md transition-colors ${
        active ? 'bg-[#1f2630] text-[#e6edf3]' : 'text-[#8b949e] hover:text-[#e6edf3]'
      } ${disabled ? 'opacity-40 cursor-not-allowed' : ''}`}
    >
      {children}
    </button>
  );
}

// ── Gallery ──────────────────────────────────────────────────────────────────

function Gallery({ brains, onOpen }: { brains: BrainSummary[] | null; onOpen: (n: string) => void }) {
  if (brains === null) return <div className="p-4 text-[#8b949e] text-[13px]">Loading brains…</div>;
  if (brains.length === 0) return <div className="p-4 text-[#8b949e] text-[13px]">No DDD brains found.</div>;
  return (
    <div className="grid gap-3 p-4" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))' }} data-testid="brainhub-gallery">
      {brains.map((b) => <BrainCard key={b.name} brain={b} onOpen={onOpen} />)}
    </div>
  );
}

function BrainCard({ brain, onOpen }: { brain: BrainSummary; onOpen: (n: string) => void }) {
  const activeStep = LIFECYCLE_STEPS.indexOf(brain.lifecycleStage);
  return (
    <button
      onClick={() => onOpen(brain.name)}
      data-testid={`brain-card-${brain.name}`}
      className="text-left rounded-lg border border-[#222831] bg-[#161b22] p-3 hover:border-[#3b4552] transition-colors"
    >
      <div className="flex items-center gap-2 mb-2">
        <span className="material-symbols-outlined text-[16px] text-[#f0a500]">psychology</span>
        <span className="text-[13px] font-semibold">{brain.name}</span>
        <span className="ml-auto text-[10px] font-mono text-[#5b636d] px-1.5 py-0.5 rounded bg-[#0e1117]">{brain.kind}</span>
      </div>

      {/* six-section presence bar */}
      <div className="flex gap-0.5 mb-2" title="six-section presence">
        {SECTION_ORDER.map((k) => (
          <span
            key={k}
            data-testid={`presence-${brain.name}-${k}`}
            className={`flex-1 h-1.5 rounded-sm ${brain.sectionsPresent[k] ? 'bg-[#3fb950]' : 'bg-[#2d333b]'}`}
          />
        ))}
      </div>

      {/* lifecycle progress */}
      <div className="flex items-center gap-1 mb-2 text-[9px] font-mono">
        {LIFECYCLE_STEPS.map((s, i) => (
          <span key={s} className={i <= activeStep ? 'text-[#3fb950]' : 'text-[#3b4552]'}>
            {s}{i < LIFECYCLE_STEPS.length - 1 ? ' ›' : ''}
          </span>
        ))}
      </div>

      {/* 4 live health signals — NO recall-heat number */}
      <div className="grid grid-cols-2 gap-1 text-[10px]">
        <Health label="Sinking" value={String(brain.health.sinking)} warn={brain.health.sinking > 0} />
        <Health label="Pending" value={String(brain.health.pending)} warn={brain.health.pending > 0} />
        <Health label="Uncommitted" value={brain.health.uncommitted ? 'yes' : 'no'} warn={brain.health.uncommitted} />
        <Health label="Last change" value={brain.health.lastChangeRelative} />
      </div>
    </button>
  );
}

function Health({ label, value, warn }: { label: string; value: string; warn?: boolean }) {
  return (
    <div className="flex items-center justify-between px-1.5 py-0.5 rounded bg-[#0e1117]">
      <span className="text-[#5b636d]">{label}</span>
      <span className={warn ? 'text-[#f0a500]' : 'text-[#8b949e]'}>{value}</span>
    </div>
  );
}

// ── Brain view ─────────────────────────────────────────────────────────────────

function BrainView({ name, agentId }: { name: string; agentId: string }) {
  const [detail, setDetail] = useState<BrainDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [previewFile, setPreviewFile] = useState<{ path: string; name: string } | null>(null);
  // #8 2-pane: which nav item is shown in the right content pane — a section
  // OR an asset projection (Specs / Code Intelligence). Asset keys ride a
  // separate channel from SectionKey (see AssetKey) so they never collide.
  const [activeKey, setActiveKey] = useState<SectionKey | AssetKey | null>(null);
  // #10: mount the existing full-screen CodeGraph overlay on demand.
  const [showGraph, setShowGraph] = useState(false);

  useEffect(() => {
    let alive = true;
    setDetail(null);
    setError(null);
    setActiveKey(null);   // reset selection when switching brains
    setShowGraph(false);
    getBrainDetail(name).then(
      (d) => alive && setDetail(d),
      (e) => alive && setError(String(e?.message ?? e)),
    );
    return () => { alive = false; };
  }, [name]);

  // ESC-routing guard (Gate-2 meta-review MED): the Brain Hub lives inside a shared
  // Modal that installs a document-level ESC→onClose listener; the full-screen
  // CodeGraph overlay (shared with BottomBar, not ours to edit) has NO ESC handler.
  // Without this, pressing ESC with the graph open would close the ENTIRE Brain Hub
  // instead of the graph. We intercept ESC in the CAPTURE phase while the graph is
  // open, close only the graph, and stop it reaching the Modal's handler.
  useEffect(() => {
    if (!showGraph) return;
    const onKeyDownCapture = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        setShowGraph(false);
      }
    };
    document.addEventListener('keydown', onKeyDownCapture, true);  // capture: runs before Modal's bubble listener
    return () => document.removeEventListener('keydown', onKeyDownCapture, true);
  }, [showGraph]);

  const openFile = useCallback((sectionMemberPath: string) => {
    // FilePreviewModal's readFile resolves `path` against the DEFAULT workspace
    // root (get_workspace_root returns the cached SwarmWS path when basePath is
    // absent). A relative basePath="Projects/<name>" would be taken as the fs
    // root verbatim (workspace.py:133 → Path(base_path)) and resolve against the
    // backend CWD → 404. So we pass NO basePath and a workspace-relative path.
    const workspaceRelPath = `Projects/${name}/${sectionMemberPath}`;
    const parts = sectionMemberPath.split('/');
    setPreviewFile({ path: workspaceRelPath, name: parts[parts.length - 1] });
  }, [name]);

  if (error) return <div className="p-4 text-[#ef4444] text-[13px]">Failed to load brain: {error}</div>;
  if (!detail) return <div className="p-4 text-[#8b949e] text-[13px]">Loading {name}…</div>;

  // Gate-1 CRITICAL: nav AND content both derive from the RUNTIME detail.sections
  // (never a hardcoded SECTION_ORDER) — so a backend that drops/reorders a section
  // can't strand the nav against a missing card. Default active = the first section
  // the backend returned; the `.find()` is guarded (active may be undefined).
  // Gate-1 F4: currentKey MUST accept an asset key too — else selecting Specs/CodeIntel
  // (not in sections) would fall through to sections[0] and the section-0 card would
  // render instead of the asset panel.
  const sections = detail.sections;
  const specs = detail.specs ?? [];
  const hasCodeIntel = detail.hasCodeIntel === true;   // daemon-skew: undefined → false
  const hasSpecs = specs.length > 0;
  const hasAssets = hasSpecs || hasCodeIntel;
  const currentKey: SectionKey | AssetKey | null =
    activeKey && (isAssetKey(activeKey) || sections.some((s) => s.key === activeKey))
      ? activeKey
      : (sections[0]?.key ?? null);
  const active = isAssetKey(currentKey)
    ? null
    : (sections.find((s) => s.key === currentKey) ?? null);

  return (
    <div className="flex flex-col h-full" data-testid="brainhub-brain">
      <div className="flex items-center gap-2 px-4 pt-4 pb-3 flex-shrink-0">
        <span className="text-[14px] font-semibold">{detail.name}</span>
        <span className="text-[10px] font-mono text-[#5b636d] px-1.5 py-0.5 rounded bg-[#161b22]">{detail.kind}</span>
        {/* #10 — open the EXISTING CodeGraph overlay for THIS brain (project={name},
            never a hardcoded literal). Gated on hasCodeIntel (a live code_intel.db
            presence check), NOT on kind: every DDD resolves to kind='knowledge'
            (aim.json carries brain_kind, never kind), so a kind gate NEVER fires —
            SwarmAI + IVTHub have a real graph but the button was unreachable. */}
        {hasCodeIntel && (
          <button
            onClick={() => setShowGraph(true)}
            data-testid="open-codegraph"
            className="ml-auto flex items-center gap-1 text-[11px] text-[#58a6ff] border border-[#1f3a5a] rounded-md px-2 py-0.5 hover:bg-[#12233a]"
            title="Open the code intelligence graph for this brain"
          >
            <span className="material-symbols-outlined text-[14px]">hub</span>
            View code graph
          </button>
        )}
      </div>

      {/* #8 — 2-pane: left section-nav + right content pane (one section at a time) */}
      <div className="flex-1 flex min-h-0">
        <nav
          data-testid="brainhub-brain-nav"
          className="w-44 flex-shrink-0 border-r border-[#222831] overflow-y-auto py-2"
        >
          {sections.map((s) => {
            const isActive = s.key === currentKey;
            return (
              <button
                key={s.key}
                data-testid={`nav-item-${s.key}`}
                onClick={() => setActiveKey(s.key)}
                className={`w-full flex items-center gap-2 text-left px-3 py-1.5 text-[12px] transition-colors ${
                  isActive ? 'bg-[#1f2630] text-[#e6edf3] border-l-2 border-[#f0a500]' : 'text-[#8b949e] hover:text-[#e6edf3] border-l-2 border-transparent'
                }`}
              >
                <span className="font-mono text-[#f0a500]">{SECTION_NUM[s.key] ?? s.num}</span>
                <span className="truncate">{s.label}</span>
                {s.members.length > 0 && (
                  <span className="ml-auto text-[9px] text-[#5b636d]">{s.members.length}</span>
                )}
              </button>
            );
          })}

          {/* Assets group — Specs + Code Intelligence are ASSET PROJECTIONS, not
              the six canonical sections (R31). Rendered as a visually-divided
              sub-group BELOW the sections; each is a nav item selected exactly like
              a section (right pane swaps). AC4: the whole group is hidden when the
              brain has neither — no empty-group noise. */}
          {hasAssets && (
            <>
              <div
                data-testid="assets-group-header"
                className="mt-2 pt-2 px-3 border-t border-[#222831] text-[9px] uppercase tracking-wider text-[#5b636d] font-semibold"
              >
                Assets
              </div>
              {hasSpecs && (
                <AssetNavItem
                  assetKey="asset:specs"
                  icon="description"
                  label="Specs"
                  count={specs.length}
                  active={currentKey === 'asset:specs'}
                  onSelect={setActiveKey}
                />
              )}
              {hasCodeIntel && (
                <AssetNavItem
                  assetKey="asset:codeintel"
                  icon="hub"
                  label="Code Intelligence"
                  active={currentKey === 'asset:codeintel'}
                  onSelect={setActiveKey}
                />
              )}
            </>
          )}
        </nav>

        {/* Gate-1 F4: EXACTLY ONE of {asset panel, section card, empty} renders —
            an if/else short-circuit, never asset-panel ALONGSIDE a section card. */}
        <div data-testid="brainhub-brain-content" className="flex-1 overflow-y-auto p-4">
          {currentKey === 'asset:specs' ? (
            // key={name} → clean remount on brain switch (avoid stale-across-brains).
            <SpecsPanel key={`specs-${name}`} specs={specs} onOpenFile={openFile} />
          ) : currentKey === 'asset:codeintel' ? (
            <CodeIntelPanel key={`ci-${name}`} project={name} />
          ) : active ? (
            <SectionCard key={active.key} section={active} onOpenFile={openFile} />
          ) : (
            <div className="text-[12px] text-[#5b636d] italic">No sections to display.</div>
          )}
        </div>
      </div>

      <FilePreviewModal
        isOpen={!!previewFile}
        onClose={() => setPreviewFile(null)}
        agentId={agentId}
        file={previewFile}
      />

      {/* #10 — sibling-mount the existing full-screen CodeGraph overlay (BottomBar
          pattern). Proven safe nested in this Modal (FilePreviewModal is the same
          shape). project={name} — NEVER a hardcoded literal (Gate-1 flag). */}
      {showGraph && (
        <CodeGraph project={name} onClose={() => setShowGraph(false)} />
      )}
    </div>
  );
}

// One left-nav item for an asset projection (Specs / Code Intelligence). Mirrors
// the section nav button's shape so the two groups read as one consistent list.
function AssetNavItem({
  assetKey, icon, label, count, active, onSelect,
}: {
  assetKey: AssetKey; icon: string; label: string; count?: number;
  active: boolean; onSelect: (k: AssetKey) => void;
}) {
  return (
    <button
      data-testid={`nav-item-${assetKey}`}
      onClick={() => onSelect(assetKey)}
      className={`w-full flex items-center gap-2 text-left px-3 py-1.5 text-[12px] transition-colors ${
        active ? 'bg-[#1f2630] text-[#e6edf3] border-l-2 border-[#58a6ff]' : 'text-[#8b949e] hover:text-[#e6edf3] border-l-2 border-transparent'
      }`}
    >
      <span className="material-symbols-outlined text-[14px] text-[#58a6ff]">{icon}</span>
      <span className="truncate">{label}</span>
      {count != null && count > 0 && (
        <span className="ml-auto text-[9px] text-[#5b636d]">{count}</span>
      )}
    </button>
  );
}

// Specs panel — spec-details/*.spec.md filenames (AC1/AC2). Now rendered in the
// right content pane when the "Specs" nav item is selected (no self-toggle — the
// nav selection IS the show/hide). Clicking a spec opens it via the same
// FilePreviewModal path the section members use.
function SpecsPanel({ specs, onOpenFile }: { specs: string[]; onOpenFile: (p: string) => void }) {
  return (
    <div className="rounded-lg border border-[#222831] bg-[#161b22] p-2.5" data-testid="specs-panel">
      <div className="flex items-center gap-2 mb-1.5 text-[12px] font-semibold">
        <span className="material-symbols-outlined text-[14px] text-[#58a6ff]">description</span>
        <span>Specs</span>
        <span className="text-[9px] text-[#5b636d] font-normal">{specs.length}</span>
      </div>
      <div className="flex flex-col gap-0.5">
        {specs.map((f) => (
          <button
            key={f}
            onClick={() => onOpenFile(`${SPEC_DETAILS_REL}/${f}`)}
            data-testid={`spec-${f}`}
            className="flex items-center gap-1.5 text-[11px] text-[#8b949e] hover:text-[#e6edf3] text-left px-1 py-0.5 rounded hover:bg-[#1f2630]"
          >
            <span className="font-mono truncate">{f}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

// Code-intel panel — reuses the EXISTING GET /api/code-intel/{project}/summary
// (getCodeIntelSummary). Still ON-DEMAND: this component only MOUNTS when its
// "Code Intelligence" nav item is selected (Gate-1 F5), so the fetch — which
// triggers find_dead_code (O(n) full scan) — never runs on brain load, only when
// the user opens the asset. Fetches on mount (not a redundant inner toggle click).
// null (no db / 404 — the service maps 404→null) or a 0-symbol/stale db renders a
// graceful line (a bare .exists() can surface an empty/foreign db — Gate-1 F6).
function CodeIntelPanel({ project }: { project: string }) {
  const [summary, setSummary] = useState<CodeIntelSummary | null>(null);
  const [state, setState] = useState<'loading' | 'loaded' | 'error'>('loading');

  useEffect(() => {
    let alive = true;
    setState('loading');
    getCodeIntelSummary(project).then(
      (s) => { if (alive) { setSummary(s); setState('loaded'); } },
      () => { if (alive) setState('error'); },
    );
    return () => { alive = false; };
  }, [project]);

  return (
    <div className="rounded-lg border border-[#222831] bg-[#161b22] p-2.5" data-testid="code-intel-panel">
      <div className="flex items-center gap-2 mb-1.5 text-[12px] font-semibold">
        <span className="material-symbols-outlined text-[14px] text-[#58a6ff]">hub</span>
        <span>Code Intelligence</span>
      </div>
      <div className="text-[11px] text-[#8b949e]" data-testid="code-intel-body">
        {state === 'loading' && <div className="italic text-[#5b636d]">Loading code map…</div>}
        {state === 'error' && <div className="italic text-[#5b636d]">Code intel unavailable.</div>}
        {state === 'loaded' && summary === null && (
          <div className="italic text-[#5b636d]">No code intelligence indexed for this brain.</div>
        )}
        {state === 'loaded' && summary && (
          <>
            <div className="mb-1">{summary.symbolCount} symbols indexed</div>
            <div className="flex flex-col gap-0.5">
              {summary.modulesTop5.map((mod) => (
                <div key={mod.name} className="flex items-center gap-2" data-testid={`ci-module-${mod.name}`}>
                  <span className="font-mono truncate">{mod.name}</span>
                  <span className="ml-auto text-[9px] text-[#5b636d]">
                    {mod.function_count}fn / {mod.class_count}cls / {mod.file_count}f
                  </span>
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function SectionCard({ section, onOpenFile }: { section: BrainSection; onOpenFile: (p: string) => void }) {
  return (
    <div className="rounded-lg border border-[#222831] bg-[#161b22] p-2.5" data-testid={`section-${section.key}`}>
      <div className="flex items-center gap-2 mb-1.5 text-[12px]">
        <span className="text-[#f0a500] font-mono">{SECTION_NUM[section.key] ?? section.num}</span>
        <span className="font-semibold">{section.label}</span>
        <span className={`text-[9px] px-1 py-0.5 rounded font-mono ${
          section.ownGovern === 'OWN' ? 'bg-[#1f3a2e] text-[#3fb950]' : 'bg-[#3a2e1f] text-[#f0a500]'
        }`}>{section.ownGovern}</span>
        <span className="ml-auto text-[10px] text-[#5b636d]">{section.curator}</span>
      </div>

      {section.members.length === 0 ? (
        <div className="text-[11px] text-[#5b636d] italic" data-testid={`empty-${section.key}`}>
          {section.completeNotBroken ? 'empty — complete, not broken' : 'empty'}
        </div>
      ) : (
        <div className="flex flex-col gap-0.5">
          {section.members.map((m) => (
            <button
              key={m.path}
              onClick={() => onOpenFile(m.path)}
              data-testid={`member-${m.path}`}
              className="flex items-center gap-1.5 text-[11px] text-[#8b949e] hover:text-[#e6edf3] text-left px-1 py-0.5 rounded hover:bg-[#1f2630]"
            >
              <span className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ background: GIT_DOT[m.gitStatus] ?? 'transparent' }} title={m.gitStatus} />
              <span className="font-mono truncate">{m.path.split('/').pop()}</span>
            </button>
          ))}
        </div>
      )}

      {section.entries.length > 0 && <KnowledgeEntries entries={section.entries} />}
    </div>
  );
}

function KnowledgeEntries({ entries }: { entries: KnowledgeEntry[] }) {
  // 7-type composition bar
  const counts: Record<string, number> = {};
  for (const e of entries) counts[e.entryType] = (counts[e.entryType] ?? 0) + 1;

  return (
    <div className="mt-2 pt-2 border-t border-[#222831]">
      <div className="flex gap-0.5 mb-1.5 h-1.5 rounded-sm overflow-hidden" title="7-type composition">
        {/* F5: STABLE order — canonical TYPE_COLOR order first (deterministic across
            brains, vs Object.keys(counts) insertion order). Gate-2: also append any
            UNKNOWN type (not in TYPE_COLOR) with the fallback color so the bar stays
            exhaustive + consistent with the per-entry dot (:below), never silently
            dropping a segment. (Backend clamps to VALID_TYPES, so unknowns are rare,
            but the bar must not disagree with the entry list if one slips through.) */}
        {[
          ...(Object.keys(TYPE_COLOR) as EntryType[]).filter((t) => counts[t] > 0),
          ...Object.keys(counts).filter((t) => !(t in TYPE_COLOR) && counts[t] > 0),
        ].map((t) => (
          <span
            key={t}
            data-testid={`typebar-${t}`}
            style={{ background: TYPE_COLOR[t as EntryType] ?? '#5b636d', flex: counts[t] }}
          />
        ))}
      </div>
      <div className="text-[10px] text-[#5b636d] mb-1">{entries.length} entries</div>
      {/* AC3: entries GROUPED BY 7-type (collapsible), not a flat list — structure
          is visible without scrolling 700 uniform titles. Group order = canonical
          TYPE_COLOR order + any unknown type appended (matches the composition bar).
          `entry-line` + `typebar-*` testids preserved (Gate-1 F5 regression guard). */}
      <div className="flex flex-col gap-1 max-h-64 overflow-auto">
        {[
          ...(Object.keys(TYPE_COLOR) as EntryType[]).filter((t) => counts[t] > 0),
          ...Object.keys(counts).filter((t) => !(t in TYPE_COLOR) && counts[t] > 0),
        ].map((t) => (
          <EntryGroup
            key={t}
            type={t}
            entries={entries.filter((e) => e.entryType === t)}
          />
        ))}
      </div>
    </div>
  );
}

// One collapsible type-group in the grouped Knowledge list (AC3). Collapsed by
// default so the 7 group headers ARE the structure the owner reads first; expand
// to see that type's entries. Caps rendered rows per group (a type can have
// hundreds) with a "+N more" line, mirroring the old flat-list cap.
function EntryGroup({ type, entries }: { type: string; entries: KnowledgeEntry[] }) {
  const [open, setOpen] = useState(false);
  const CAP = 40;
  return (
    <div data-testid={`entry-group-${type}`}>
      <button
        onClick={() => setOpen((v) => !v)}
        data-testid={`entry-group-toggle-${type}`}
        className="w-full flex items-center gap-1.5 text-[10px] text-left px-1 py-0.5 rounded hover:bg-[#1f2630]"
      >
        <span className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ background: TYPE_COLOR[type as EntryType] ?? '#5b636d' }} />
        <span className="font-mono text-[#8b949e]">{type}</span>
        <span className="text-[9px] text-[#5b636d]">{entries.length}</span>
        <span className="ml-auto text-[9px] text-[#5b636d]">{open ? '▾' : '▸'}</span>
      </button>
      {open && (
        <div className="flex flex-col gap-0.5 pl-3">
          {entries.slice(0, CAP).map((e, i) => (
            <div key={`${e.file}-${i}`} className="flex items-center gap-1.5 text-[10px]" data-testid="entry-line">
              <span className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ background: TYPE_COLOR[e.entryType] ?? '#5b636d' }} title={e.entryType} />
              <span className={`truncate font-mono ${DECAY_STYLE[e.decayState] ?? DECAY_STYLE.active}`}>{e.title}</span>
            </div>
          ))}
          {entries.length > CAP && <div className="text-[10px] text-[#5b636d] italic">+{entries.length - CAP} more…</div>}
        </div>
      )}
    </div>
  );
}

// ── Review view (Run 2) ──────────────────────────────────────────────────────

function shortSha(sha: string): string {
  return sha ? sha.slice(0, 8) : '—';
}

function ReviewView({ name }: { name: string }) {
  const [data, setData] = useState<ReviewData | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Per-ACTION errors (reject/proposal 409s etc.) are TRANSIENT + inline — they must
  // NOT blank the whole queue via the top-level `if (error)` return (Gate-2: a routine
  // retryable 409 shouldn't wipe the review view). Separate channel from the load error.
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // H2: "Mark all seen" advances the watermark IRREVERSIBLY (wipes the review
  // queue from the diff). Require a two-click armed confirm so a single stray
  // click can't wipe it. `armed` MUST reset on any context change (reload after
  // any action, brain switch) so it never persists stale — reset in load() below.
  const [armed, setArmed] = useState(false);

  const load = useCallback(() => {
    let alive = true;
    setData(null);
    setError(null);
    setActionError(null);   // clear any transient action error on (re)load
    setArmed(false);   // disarm on every (re)load: brain switch, or after any action
    getReview(name).then(
      (d) => alive && setData(d),
      (e) => alive && setError(String(e?.message ?? e)),
    );
    return () => { alive = false; };
  }, [name]);

  useEffect(() => load(), [load]);  // load() returns its own alive-cleanup, invoked on unmount/re-run

  const onApproveAll = useCallback(async () => {
    if (!armed) { setArmed(true); return; }   // first click arms; no API call yet
    setBusy(true);
    // ALWAYS disarm (finally) — Gate-2: if approveReview throws, armed must NOT stay
    // stuck true (a stale-armed button would let the next single click re-fire the
    // POST, bypassing the two-click guard). Surface the error instead of swallowing.
    try {
      await approveReview(name);
      load();
    } catch (e) {
      setActionError(String((e as { message?: string })?.message ?? e));
    } finally {
      setArmed(false);
      setBusy(false);
    }
  }, [name, load, armed]);

  const onRejectHunk = useCallback(async (h: ReviewHunk) => {
    setBusy(true);
    // F3: surface API errors (parity with onApproveAll) via the TRANSIENT actionError
    // channel — a rejected git apply -R (409/404/500) must not fail silently, but it
    // also must NOT blank the whole queue (Gate-2: a routine retryable 409 is inline).
    try {
      await rejectReviewHunk(name, h.file, h.signature);
      load();
    } catch (e) {
      setActionError(String((e as { message?: string })?.message ?? e));
    } finally { setBusy(false); }
  }, [name, load]);

  const onProposal = useCallback(async (p: PendingProposal, accept: boolean) => {
    setBusy(true);
    try {
      if (accept) await approveProposal(p.id, name);
      else await rejectProposal(p.id, name);
      load();
    } catch (e) {
      setActionError(String((e as { message?: string })?.message ?? e));
    } finally { setBusy(false); }
  }, [name, load]);

  if (error) return <div className="p-4 text-[#ef4444] text-[13px]" data-testid="review-error">Failed to load review: {error}</div>;
  if (!data) return <div className="p-4 text-[#8b949e] text-[13px]">Loading review…</div>;

  // Zone A = auto-applied hunks; Zone C = pending risky proposals. (F1: the former
  // Zone B "decay·sinking" was removed — the backend never emitted that tag, so it
  // was a permanently-empty misleading zone; the Gallery's health.sinking count
  // already surfaces dormant/archived entries.)
  const zoneA = data.hunks.filter((h) => h.tag === 'cultivation·auto-applied');
  const riskyHunks = data.hunks.filter((h) => h.tag === 'risky·staged');

  return (
    <div className="p-4" data-testid="brainhub-review">
      {/* diff header */}
      <div className="flex items-center gap-2 mb-3 text-[11px] font-mono text-[#8b949e]" data-testid="review-diff-header">
        <span className="material-symbols-outlined text-[15px] text-[#a855f7]">commit</span>
        diff <span className="text-[#e6edf3]">Projects/{name}/</span>
        · last-reviewed <span className="text-[#e6edf3]">{shortSha(data.last_reviewed_sha)}</span>
        → HEAD <span className="text-[#e6edf3]">{shortSha(data.head_sha)}</span>
        <button
          onClick={onApproveAll}
          // F8: NEVER advance the watermark when the diff is incomplete (timed out) —
          // the empty/partial hunk list would silently mark unreviewed work as seen.
          disabled={busy || data.hunks.length === 0 || data.diff_incomplete}
          data-testid="review-approve-all"
          className={`ml-auto flex items-center gap-1 text-[11px] rounded-md px-2 py-0.5 disabled:opacity-40 ${
            armed
              ? 'text-[#f0a500] border border-[#5a4a1f] bg-[#241f10] hover:bg-[#2e2814]'
              : 'text-[#3fb950] border border-[#1f5a2a] hover:bg-[#132918]'
          }`}
          title={armed ? 'This advances the watermark and clears the review queue — click again to confirm' : undefined}
        >
          <span className="material-symbols-outlined text-[14px]">{armed ? 'warning' : 'visibility'}</span>
          {armed ? 'Click again to confirm — advances watermark' : 'Mark all seen → advance watermark'}
        </button>
      </div>

      {/* F8: loud degraded state — the diff timed out, so the queue below is INCOMPLETE.
          Gate-2: give an explicit Retry affordance (not just "retry shortly" text) so a
          large-repo timeout isn't a dead-end lockout of the review. */}
      {data.diff_incomplete && (
        <div className="flex items-center gap-1.5 mb-3 text-[11px] text-[#f0a500] bg-[#241f10] border border-[#5a4a1f] rounded-md px-2 py-1" data-testid="review-diff-incomplete">
          <span className="material-symbols-outlined text-[14px]">warning</span>
          The review diff timed out — this queue may be incomplete. "Mark all seen" is disabled to avoid skipping unreviewed changes.
          <button onClick={() => load()} disabled={busy} data-testid="review-diff-retry"
            className="ml-auto flex items-center gap-1 text-[10px] text-[#f0a500] border border-[#5a4a1f] rounded px-1.5 py-0.5 hover:bg-[#2e2814] disabled:opacity-40">
            <span className="material-symbols-outlined text-[12px]">refresh</span>Retry
          </button>
        </div>
      )}

      {/* F3: transient per-action error (reject/proposal 409 etc.) — inline, does NOT
          blank the whole queue (Gate-2). Dismissible; also cleared on next load(). */}
      {actionError && (
        <div className="flex items-center gap-1.5 mb-3 text-[11px] text-[#ff9a94] bg-[#2a1214] border border-[#5a1f1f] rounded-md px-2 py-1" data-testid="review-action-error">
          <span className="material-symbols-outlined text-[14px]">error</span>
          Action failed: {actionError}
          <button onClick={() => setActionError(null)} data-testid="review-action-error-dismiss"
            className="ml-auto text-[10px] text-[#8b949e] hover:text-[#e6edf3] px-1.5">dismiss</button>
        </div>
      )}

      {/* Zone A — auto-cultivated (already committed) */}
      <ReviewZone
        testid="review-zone-a" title="Auto-cultivated · already in brain"
        desc="applied + git-committed — NOT awaiting approval. Reject reverts that hunk."
        color="#58a6ff"
      >
        {zoneA.length === 0 ? <ZoneEmpty text="no auto-applied changes since last review" />
          : zoneA.map((h) => (
            <HunkCard key={h.signature} hunk={h} busy={busy}
              onReject={() => onRejectHunk(h)} />
          ))}
      </ReviewZone>

      {/* Zone C — pending risky proposals (the ONLY true Approve/Reject gate) */}
      <ReviewZone
        testid="review-zone-c" title="Pending approval · NOT yet in brain"
        desc="risky proposals in .artifacts/proposals/ — the real gate."
        color="#f0a500"
      >
        {data.proposals.length === 0 && riskyHunks.length === 0
          ? <ZoneEmpty text="no proposals awaiting decision" />
          : data.proposals.map((p) => (
            <div key={p.id} className="rounded-md border border-dashed border-[#3a2e12] bg-[#1a1710] p-2 mb-1.5" data-testid="review-proposal">
              <div className="flex items-center gap-1.5 mb-1 text-[11px]">
                <span className="font-mono text-[#f0a500]">{p.target_doc}</span>
                <span className="text-[#5b636d]">· {p.target_section}</span>
                {/* F4: confidence is the human-gate decision signal — render it
                    (null-guarded: an un-scored proposal shows "—", never "null"). */}
                <span className="text-[10px] text-[#8b949e]" data-testid="proposal-confidence" title="cultivation confidence">
                  conf {p.confidence != null ? p.confidence.toFixed(2) : '—'}
                </span>
                <div className="ml-auto flex gap-1">
                  <button onClick={() => onProposal(p, true)} disabled={busy}
                    className="text-[10px] text-[#3fb950] border border-[#1f5a2a] rounded px-1.5 py-0.5 hover:bg-[#132918] disabled:opacity-40">Approve</button>
                  <button onClick={() => onProposal(p, false)} disabled={busy}
                    className="text-[10px] text-[#ef4444] border border-[#5a1f1f] rounded px-1.5 py-0.5 hover:bg-[#2a1214] disabled:opacity-40">Reject</button>
                </div>
              </div>
              <div className="text-[10px] text-[#8b949e] line-clamp-2">{p.content}</div>
            </div>
          ))}
      </ReviewZone>
    </div>
  );
}

function ReviewZone({ testid, title, desc, color, children }: {
  testid: string; title: string; desc: string; color: string; children: React.ReactNode;
}) {
  return (
    <div className="mb-4" data-testid={testid}>
      <div className="flex items-center gap-2 mb-1.5">
        <span className="text-[12px] font-semibold" style={{ color }}>{title}</span>
        <span className="text-[10px] text-[#5b636d]">{desc}</span>
      </div>
      {children}
    </div>
  );
}

function ZoneEmpty({ text }: { text: string }) {
  return <div className="text-[11px] text-[#5b636d] italic px-1 py-1">{text}</div>;
}

function HunkCard({ hunk, busy, onReject }: { hunk: ReviewHunk; busy: boolean; onReject: () => void }) {
  return (
    <div className="rounded-md border border-[#222831] bg-[#12161c] mb-1.5 overflow-hidden" data-testid="review-hunk">
      <div className="flex items-center gap-1.5 px-2 py-1 border-b border-[#222831]">
        <span className="font-mono text-[10px] text-[#8b949e]">{hunk.file}</span>
        <button
          onClick={onReject}
          disabled={busy}
          data-testid="review-reject-hunk"
          className="ml-auto flex items-center gap-1 text-[10px] text-[#ef4444] border border-[#5a1f1f] rounded px-1.5 py-0.5 hover:bg-[#2a1214] disabled:opacity-40"
        >
          <span className="material-symbols-outlined text-[13px]">undo</span>
          Revert hunk
        </button>
      </div>
      <pre className="text-[10px] font-mono leading-relaxed px-2 py-1 overflow-x-auto max-h-40">
        {hunk.diff_text.split('\n').slice(0, 20).map((ln, i) => {
          const c = ln.startsWith('+') && !ln.startsWith('+++') ? '#7ee787'
            : ln.startsWith('-') && !ln.startsWith('---') ? '#ff9a94'
            : '#5b636d';
          return <div key={i} style={{ color: c }}>{ln || ' '}</div>;
        })}
      </pre>
    </div>
  );
}

// ── Distribute view (Run 3) ──────────────────────────────────────────────────

function DistributeView({ name }: { name: string }) {
  const [data, setData] = useState<DistributionState | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setData(null);
    setError(null);
    getDistribution(name).then(
      (d) => alive && setData(d),
      (e) => alive && setError(String(e?.message ?? e)),
    );
    return () => { alive = false; };
  }, [name]);

  // [Distribute a brain] does NOT auto-run — s_ddd-distribute is human-in-the-loop
  // (confirms targets + content-safety scan + emit≠publish). We surface the exact
  // chat command; the user invokes it in a chat tab where the HITL gate runs.
  const distributeCmd = `distribute this ddd: ${name}`;
  const [copied, setCopied] = useState(false);
  const onDistribute = useCallback(() => {
    // Guard BOTH the method (clipboard may be absent in an older webview) AND the
    // returned promise (?. on .then too) — Gate-2 HIGH: `clipboard?.writeText(x).then`
    // still throws if clipboard exists but writeText returns undefined.
    const p = navigator.clipboard?.writeText(distributeCmd);
    p?.then(
      () => { setCopied(true); setTimeout(() => setCopied(false), 2000); },
      () => {},
    );
  }, [distributeCmd]);

  if (error) return <div className="p-4 text-[#ef4444] text-[13px]" data-testid="distribute-error">Failed to load distribution: {error}</div>;
  if (!data) return <div className="p-4 text-[#8b949e] text-[13px]">Loading distribution…</div>;

  return (
    <div className="p-4" data-testid="brainhub-distribute">
      {/* declared reach */}
      {data.distributable ? (
        <>
          <div className="flex items-center gap-2 mb-3 text-[12px]">
            <span className="material-symbols-outlined text-[16px] text-[#3fb950]">outbound</span>
            <span className="font-semibold text-[#e6edf3]">Distributable</span>
            <span className={`text-[10px] px-1.5 py-0.5 rounded font-mono ${data.visibility === 'external' ? 'bg-[#3a2412] text-[#f0a500]' : 'bg-[#1f2630] text-[#8b949e]'}`}>
              {data.visibility}
            </span>
          </div>
          <div className="flex flex-col gap-1.5 mb-4" data-testid="distribute-targets">
            {data.declared_targets.map((t) => (
              <div key={t} className="flex items-center gap-2 rounded-md border border-[#222831] bg-[#12161c] px-2.5 py-1.5" data-testid="distribute-target-row">
                <span className="material-symbols-outlined text-[14px] text-[#58a6ff]">deployed_code</span>
                <span className="font-mono text-[11px] text-[#e6edf3]">{t}</span>
                {/* F2 TRISTATE — three EXPLICIT branches. `null` (freshness unknown,
                    uncommitted output) must NOT fall into "up to date" (the old
                    `!source_changed_since` did exactly that, re-burying staleness). */}
                {!data.has_output ? (
                  <span className="ml-auto text-[9px] text-[#5b636d]">never distributed</span>
                ) : data.source_changed_since === true ? (
                  <span className="ml-auto text-[9px] text-[#f0a500]" title="knowledge changed since last distribute">● source changed since last distribute</span>
                ) : data.source_changed_since === false ? (
                  <span className="ml-auto text-[9px] text-[#5b636d]">up to date</span>
                ) : (
                  <span className="ml-auto text-[9px] text-[#8b949e]" title="the distribute output isn't git-committed, so there's no stable anchor to compare against — commit the output to enable freshness tracking">freshness unknown</span>
                )}
              </div>
            ))}
          </div>
          {data.has_output && (
            <div className="text-[10px] text-[#5b636d] mb-3 font-mono">
              last output: {data.output_path} {data.last_distribute_time ? `· ${data.last_distribute_time.slice(0, 10)}` : ''}
            </div>
          )}
        </>
      ) : (
        <div className="rounded-md border border-dashed border-[#3a2e12] bg-[#1a1710] p-3 mb-4" data-testid="distribute-not-distributable">
          <div className="flex items-center gap-2 text-[12px] text-[#f0a500] mb-1">
            <span className="material-symbols-outlined text-[16px]">block</span>
            Not distributable
          </div>
          <div className="text-[10px] text-[#8b949e] leading-relaxed">
            This brain has no <span className="font-mono">distribution</span> block in its <span className="font-mono">aim.json</span>.
            The owner must declare a reach before it can be distributed — add to <span className="font-mono">aim.json</span>:
            <code className="block mt-1 px-2 py-1 rounded bg-[#0e1117] text-[#8b949e] whitespace-pre">{'"distribution": { "targets": ["open-plugin"], "visibility": "internal" }'}</code>
            {data.warnings.length > 0 && <span className="block mt-1 text-[#ff9a94]">⚠ {data.warnings.join('; ')}</span>}
            {/* Gate-2 MED: a stale output with the block since removed — surface it, don't hide it. */}
            {data.has_output && (
              <span className="block mt-1 text-[#db8c3a]" data-testid="distribute-stale-output">
                ⚠ an orphaned distribute output still exists (<span className="font-mono">{data.output_path}</span>) — the reach was declared before, then removed.
              </span>
            )}
          </div>
        </div>
      )}

      {/* [Distribute a brain] — guidance, not auto-run (HITL) */}
      <div className="flex items-center gap-2">
        <button
          onClick={onDistribute}
          disabled={!data.distributable}
          data-testid="distribute-button"
          className="flex items-center gap-1.5 text-[11px] text-[#3fb950] border border-[#1f5a2a] rounded-md px-2.5 py-1 hover:bg-[#132918] disabled:opacity-40 disabled:cursor-not-allowed"
          title={data.distributable ? 'Copy the chat command to run s_ddd-distribute' : 'Declare a distribution block first'}
        >
          <span className="material-symbols-outlined text-[14px]">content_copy</span>
          {copied ? 'Copied — paste into a chat tab' : 'Distribute a brain'}
        </button>
        {data.distributable && (
          <span className="text-[10px] text-[#5b636d] font-mono">→ {distributeCmd}</span>
        )}
      </div>
    </div>
  );
}
