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
import { getBrains, getBrainDetail } from '../../services/ddd';
import type {
  BrainSummary, BrainDetail, BrainSection, KnowledgeEntry, EntryType, DecayState, SectionKey,
} from '../../services/ddd';
import { agentsService } from '../../services/agents';
import { FilePreviewModal } from '../workspace/FilePreviewModal';

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

const GIT_DOT: Record<string, string> = {
  clean: 'transparent', modified: '#f0a500', added: '#3fb950',
  untracked: '#8b949e', deleted: '#ef4444', renamed: '#a855f7', conflicting: '#ef4444',
};

const LIFECYCLE_STEPS = ['CREATE', 'GROW', 'REVIEW', 'DISTRIBUTE'] as const;

// ── Root ───────────────────────────────────────────────────────────────────────

type Tab = 'gallery' | 'brain';

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
      </div>

      <div className="flex-1 overflow-auto">
        {error && <div className="p-4 text-[#ef4444] text-[13px]" data-testid="brainhub-error">Failed to load brains: {error}</div>}
        {!error && tab === 'gallery' && <Gallery brains={brains} onOpen={openBrain} />}
        {!error && tab === 'brain' && selected && <BrainView name={selected} agentId={agentId} />}
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

  useEffect(() => {
    let alive = true;
    setDetail(null);
    setError(null);
    getBrainDetail(name).then(
      (d) => alive && setDetail(d),
      (e) => alive && setError(String(e?.message ?? e)),
    );
    return () => { alive = false; };
  }, [name]);

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

  return (
    <div className="p-4" data-testid="brainhub-brain">
      <div className="flex items-center gap-2 mb-3">
        <span className="text-[14px] font-semibold">{detail.name}</span>
        <span className="text-[10px] font-mono text-[#5b636d] px-1.5 py-0.5 rounded bg-[#161b22]">{detail.kind}</span>
      </div>

      <div className="flex flex-col gap-2">
        {detail.sections.map((s) => <SectionCard key={s.key} section={s} onOpenFile={openFile} />)}
      </div>

      <FilePreviewModal
        isOpen={!!previewFile}
        onClose={() => setPreviewFile(null)}
        agentId={agentId}
        file={previewFile}
      />
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
        {(Object.keys(counts) as EntryType[]).map((t) => (
          <span
            key={t}
            data-testid={`typebar-${t}`}
            style={{ background: TYPE_COLOR[t] ?? '#5b636d', flex: counts[t] }}
          />
        ))}
      </div>
      <div className="text-[10px] text-[#5b636d] mb-1">{entries.length} entries</div>
      <div className="flex flex-col gap-0.5 max-h-48 overflow-auto">
        {entries.slice(0, 60).map((e, i) => (
          <div key={`${e.file}-${i}`} className="flex items-center gap-1.5 text-[10px]" data-testid="entry-line">
            <span className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ background: TYPE_COLOR[e.entryType] ?? '#5b636d' }} title={e.entryType} />
            <span className={`truncate font-mono ${DECAY_STYLE[e.decayState]}`}>{e.title}</span>
          </div>
        ))}
        {entries.length > 60 && <div className="text-[10px] text-[#5b636d] italic">+{entries.length - 60} more…</div>}
      </div>
    </div>
  );
}
