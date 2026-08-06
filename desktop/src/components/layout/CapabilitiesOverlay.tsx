/**
 * CapabilitiesOverlay — the left-nav "Capabilities" surface: "what your AI can do".
 *
 * Opens on `swarm:show-capabilities` (registered in ALL_SHOW_EVENTS + overlaySurfaces;
 * agent-openable via UI_COMMAND_ALLOWLIST — payload-less show-only). Promotes
 * Capabilities from a Settings tab to a first-class user-facing domain (run_b5d98151).
 *
 * Two views inside the host frame (Skills | Connections):
 *   • SKILLS      — abilities browsed by CURATED CATEGORY, with the signature skills
 *                   (pipeline, pollinate) as hero cards. Rows are plain-language
 *                   (icon + name + one-line description) — NO version/tier/folderName
 *                   badge (that dev metadata lives in the detail drawer, not the row;
 *                   design-judgment checks 2/3). The owner-only "Internal" group is
 *                   backend-filtered (a non-owner runtime never receives internal
 *                   skills — see routers/skills.py) AND rendered only when >=1 internal
 *                   skill is present (empty group renders NOTHING, never a void).
 *   • CONNECTIONS — status-first projection of GET /api/mcp: "Connected" (enabled) vs
 *                   "Available" (disabled catalog), with an in-place toggle. Raw
 *                   connectionType/config JSON is NOT here (it lives in Settings'
 *                   advanced path); this view is about "is this tool on?".
 *
 * DETAIL DRAWER: clicking a skill row opens an absolute right-side drawer (layered,
 * NOT a flex sibling — the list never compresses). It carries the plain description,
 * the trigger phrase, and THEN the demoted dev metadata (tier / version / folder).
 *
 * CTAs (bottom): "Teach Swarm a new skill" (dispatches the AI-native create flow to a
 * chat tab — the preferred path) and "Connect a tool" (Connections view). Create-skill
 * is intentionally the chat path (Gate-1 fix D: the flow stays reachable after the
 * Settings Skills tab is deleted — it is NOT orphaned).
 *
 * Fail-safe (load-bearing, run_b5d98151 §5): a skill missing category/visibility falls
 * to Utilities/public (the service mapper defaults them); an empty category group is
 * never rendered. Neither path throws.
 *
 * @exports CapabilitiesContent
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { skillsService } from '../../services/skills';
import { mcpConfigService, type ConfigEntry } from '../../services/mcpConfig';
import { classifyLoadError } from '../../services/api';
import type { Skill } from '../../types';
import { WorkbenchToolbar, OverlayDrawer } from './overlayShell';

export interface CapabilitiesContentProps {
  /** Land a prompt into a chat tab (used by "Teach Swarm a new skill"). Returns true
   *  if it landed (→ host closes the overlay). */
  onDispatch: (prompt: string) => boolean;
  /** Host-owned close. */
  close: () => void;
}

type ViewMode = 'skills' | 'connections';

/** The two signature abilities — rendered as heroes, the ONE thing that stands out.
 *  Keys are the exact folder_name (hyphenated, s_ prefix) — matched by `in SIGNATURE`. */
const SIGNATURE: Record<string, { icon: string; blurb: string }> = {
  's_autonomous-pipeline': { icon: '🚀', blurb: 'One sentence in → PR-ready code out. The delivery engine.' },
  's_pollinate': { icon: '🌸', blurb: 'Your message → poster, video, narrative — the right media, auto.' },
};

/** Category display order — the rest fall in alphabetically after these; Internal LAST. */
const CATEGORY_ORDER = [
  'Research', 'Content', 'Writing', 'Development', 'Automation',
  'Integrations', 'Workspace', 'Memory', 'Ops', 'UI', 'System', 'Utilities',
];

/** Order categories deterministically: known order first, unknown alpha, Internal last. */
export function orderedCategories(cats: string[]): string[] {
  const known = CATEGORY_ORDER.filter((c) => cats.includes(c));
  const rest = cats
    .filter((c) => c !== 'Internal' && !CATEGORY_ORDER.includes(c))
    .sort((a, b) => a.localeCompare(b));
  const internal = cats.includes('Internal') ? ['Internal'] : [];
  return [...known, ...rest, ...internal];
}

/** Group visible skills by category. Signature skills are pulled OUT into heroes so
 *  they don't also appear as a plain row. Pure — safe on [] (renders no groups). */
export function groupSkills(skills: Skill[]): { heroes: Skill[]; groups: [string, Skill[]][] } {
  const heroes = skills.filter((s) => s.folderName in SIGNATURE);
  const rest = skills.filter((s) => !(s.folderName in SIGNATURE));
  const byCat = new Map<string, Skill[]>();
  for (const s of rest) {
    const cat = s.category || 'Utilities';
    if (!byCat.has(cat)) byCat.set(cat, []);
    byCat.get(cat)!.push(s);
  }
  const groups = orderedCategories([...byCat.keys()])
    .map((c) => [c, byCat.get(c)!.sort((a, b) => a.name.localeCompare(b.name))] as [string, Skill[]])
    .filter(([, list]) => list.length > 0); // never emit an empty group (§5 fail-safe)
  return { heroes, groups };
}

export function CapabilitiesContent({ onDispatch, close }: CapabilitiesContentProps) {
  const [view, setView] = useState<ViewMode>('skills');
  const [skills, setSkills] = useState<Skill[]>([]);
  const [mcps, setMcps] = useState<ConfigEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<Skill | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [sk, mc] = await Promise.all([
        skillsService.list(),
        mcpConfigService.listAll().catch(() => [] as ConfigEntry[]),
      ]);
      setSkills(sk);
      setMcps(mc);
    } catch (e) {
      setError(classifyLoadError(e, 'Capabilities'));
    } finally {
      setLoading(false);
    }
  }, []);

  // load() is a stable useCallback (deps []); depending on it here fetches once on mount.
  useEffect(() => { void load(); }, [load]);

  // Search filters the visible skill set; grouping + hero extraction happen after.
  const visibleSkills = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return skills;
    return skills.filter(
      (s) => s.name.toLowerCase().includes(q) || s.description.toLowerCase().includes(q) || s.category.toLowerCase().includes(q),
    );
  }, [skills, query]);

  const { heroes, groups } = useMemo(() => groupSkills(visibleSkills), [visibleSkills]);

  const connected = useMemo(() => mcps.filter((m) => m.enabled), [mcps]);
  const available = useMemo(() => mcps.filter((m) => !m.enabled && m.layer === 'catalog'), [mcps]);

  const toggleMcp = useCallback(async (m: ConfigEntry) => {
    const target = !m.enabled;
    // Optimistic flip.
    setMcps((prev) => prev.map((x) => (x.id === m.id ? { ...x, enabled: target } : x)));
    try {
      if (m.layer === 'catalog') {
        await mcpConfigService.updateCatalogEntry(m.id, { enabled: target });
      } else {
        await mcpConfigService.updateDevEntry(m.id, { enabled: target });
      }
    } catch {
      // The write failed → the optimistic flip is a lie. Revert THIS entry locally
      // (never leave the UI showing a state the backend doesn't hold), then try a
      // full reload to re-sync; if the reload also fails, the local revert already
      // restored truth, so the UI is never stuck in a fabricated state.
      setMcps((prev) => prev.map((x) => (x.id === m.id ? { ...x, enabled: m.enabled } : x)));
      void load();
    }
  }, [load]);

  const teachNewSkill = useCallback(() => {
    const landed = onDispatch(
      'I want to teach you a new skill. Ask me what it should do, then use s_skill-builder to create it.',
    );
    if (landed) close();
  }, [onDispatch, close]);

  return (
    <div className="flex-1 flex flex-col overflow-hidden relative" data-testid="capabilities-overlay">
      <WorkbenchToolbar
        testid="capabilities-toolbar"
        loading={loading}
        left={
          <div className="flex items-center gap-1 rounded-lg border border-[var(--color-border)] p-0.5">
            {(['skills', 'connections'] as ViewMode[]).map((v) => (
              <button
                key={v}
                data-testid={`cap-view-${v}`}
                onClick={() => setView(v)}
                className={
                  'px-3 py-1 text-sm rounded-md ' +
                  (view === v
                    ? 'bg-[var(--color-primary)] text-white font-medium'
                    : 'text-[var(--color-text-muted)] hover:bg-[var(--color-hover)]')
                }
              >
                {v === 'skills' ? 'Skills' : 'Connections'}
              </button>
            ))}
          </div>
        }
        right={
          view === 'skills' ? (
            <input
              data-testid="cap-search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search abilities…"
              className="w-64 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-md px-3 py-1 text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-faint)]"
            />
          ) : null
        }
      />

      <div className="flex-1 overflow-y-auto px-6 py-4">
        {error && (
          <div className="text-sm text-[var(--color-text-muted)] py-8 text-center" data-testid="cap-error">
            {error}
          </div>
        )}

        {!error && view === 'skills' && (
          <div data-testid="cap-skills-view">
            {/* Signature heroes — the ONE dominant element */}
            {heroes.length > 0 && (
              <div className="grid grid-cols-2 gap-3 mb-6" data-testid="cap-heroes">
                {heroes.map((h) => (
                  <button
                    key={h.folderName}
                    onClick={() => setSelected(h)}
                    className="text-left rounded-xl border border-[var(--color-border-strong)] p-4 hover:bg-[var(--color-hover)]"
                    // Token-driven tint: the host sets --panel-accent to this overlay's
                    // zone color (Work/teal); color-mix keeps it theme-consistent (no
                    // hardcoded hex — navcard/overlay standard §1). Fallback to primary.
                    style={{ background: 'linear-gradient(135deg, color-mix(in srgb, var(--panel-accent, var(--color-primary)) 12%, transparent), transparent)' }}
                  >
                    <div className="text-[10px] font-mono tracking-wider text-[var(--color-primary)]">⭐ SIGNATURE</div>
                    <div className="text-base font-semibold mt-2 flex items-center gap-2">
                      <span>{SIGNATURE[h.folderName]?.icon ?? '✨'}</span>{h.name}
                    </div>
                    <div className="text-[13px] text-[var(--color-text-muted)] mt-1">
                      {SIGNATURE[h.folderName]?.blurb ?? h.description}
                    </div>
                  </button>
                ))}
              </div>
            )}

            {/* Category groups — no empty group ever renders (§5) */}
            {groups.map(([cat, list]) => (
              <div key={cat} className="mb-6" data-testid={`cap-group-${cat}`}>
                <h2 className="text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wide mb-1 flex items-baseline gap-2">
                  {cat}
                  <span className="text-[11px] font-mono text-[var(--color-text-faint)]">{list.length}</span>
                </h2>
                <div className="grid grid-cols-2 gap-x-6">
                  {list.map((s) => (
                    <button
                      key={s.folderName}
                      data-testid={`cap-skill-${s.folderName}`}
                      onClick={() => setSelected(s)}
                      className="text-left flex items-start gap-2 px-2 py-2 rounded-md hover:bg-[var(--color-hover)] min-w-0"
                    >
                      <span className="text-[var(--color-text)] text-sm font-medium truncate">{s.name}</span>
                      <span className="text-xs text-[var(--color-text-faint)] truncate">{s.description}</span>
                    </button>
                  ))}
                </div>
              </div>
            ))}

            {!loading && groups.length === 0 && heroes.length === 0 && (
              <div className="text-sm text-[var(--color-text-faint)] py-8 text-center" data-testid="cap-empty">
                {query ? 'No abilities match your search.' : 'No abilities found.'}
              </div>
            )}
          </div>
        )}

        {!error && view === 'connections' && (
          <div data-testid="cap-connections-view">
            <ConnGroup title="Connected" tag={`${connected.length} live`} entries={connected} onToggle={toggleMcp} statusLabel="CONNECTED" statusClass="text-[var(--color-primary)]" />
            <ConnGroup title="Available — one click to connect" tag={`${available.length}`} entries={available} onToggle={toggleMcp} statusLabel="AVAILABLE" statusClass="text-[var(--color-text-muted)]" />
            {!loading && connected.length === 0 && available.length === 0 && (
              <div className="text-sm text-[var(--color-text-faint)] py-8 text-center">No connections configured.</div>
            )}
          </div>
        )}
      </div>

      {/* Bottom CTAs */}
      <div className="flex items-center gap-3 px-6 py-3 border-t border-[var(--color-border)] bg-[var(--color-bg-chrome)]">
        {view === 'skills' ? (
          <button
            data-testid="cap-teach-skill"
            onClick={teachNewSkill}
            className="px-3 py-2 rounded-lg bg-[var(--color-primary)] text-white text-sm font-medium"
          >
            ✨ Teach Swarm a new skill
          </button>
        ) : (
          <span className="text-[11px] text-[var(--color-text-faint)]">
            A greyed connection means auth expired (e.g. Midway) — re-auth from the tool's Enable button.
          </span>
        )}
      </div>

      {/* Detail drawer — dev metadata demoted here, off the row */}
      {selected && (
        <div className="absolute inset-0 z-[9]" onClick={() => setSelected(null)}>
          <OverlayDrawer widthPx={380} testid="cap-detail-drawer">
            <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--color-border)]">
              <div className="text-sm font-semibold">{selected.name}</div>
              <button onClick={() => setSelected(null)} className="text-[var(--color-text-faint)] hover:text-[var(--color-text)]">✕</button>
            </div>
            <div className="p-4 space-y-4 overflow-y-auto text-sm">
              <p className="text-[var(--color-text-muted)]">{selected.description}</p>
              <div>
                <div className="text-[11px] uppercase tracking-wide text-[var(--color-text-faint)] mb-1">How to use</div>
                <div className="text-[13px]">Say what you want in chat — Swarm invokes it automatically.</div>
              </div>
              {/* Demoted dev metadata */}
              <div className="pt-3 border-t border-[var(--color-border)] text-[12px] text-[var(--color-text-faint)] font-mono space-y-1">
                <div>source: {selected.sourceTier}</div>
                <div>version: {selected.version}</div>
                <div>id: {selected.folderName}</div>
                {selected.visibility === 'internal' && <div className="text-[var(--color-text-muted)]">🔒 internal (owner-only)</div>}
              </div>
            </div>
          </OverlayDrawer>
        </div>
      )}
    </div>
  );
}

function ConnGroup({
  title, tag, entries, onToggle, statusLabel, statusClass,
}: {
  title: string;
  tag: string;
  entries: ConfigEntry[];
  onToggle: (m: ConfigEntry) => void;
  statusLabel: string;
  statusClass: string;
}) {
  if (entries.length === 0) return null; // never render an empty group
  return (
    <div className="mb-6" data-testid={`cap-conn-${statusLabel.toLowerCase()}`}>
      <h2 className="text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wide mb-1 flex items-baseline gap-2">
        {title}
        <span className="text-[11px] font-mono text-[var(--color-text-faint)]">{tag}</span>
      </h2>
      {entries.map((m) => (
        <div key={m.id} className="flex items-center gap-3 px-2 py-3 rounded-md hover:bg-[var(--color-hover)]">
          <div className="min-w-0">
            <div className="text-sm font-medium truncate">{m.name}</div>
            {m.description && <div className="text-xs text-[var(--color-text-faint)] truncate">{m.description}</div>}
          </div>
          <div className="flex-1" />
          <span className={`text-[11px] font-mono font-medium ${statusClass}`}>{statusLabel}</span>
          <button
            data-testid={`cap-toggle-${m.id}`}
            onClick={() => onToggle(m)}
            aria-label={`toggle ${m.name}`}
            className={
              'w-9 h-5 rounded-full relative transition-colors ' +
              (m.enabled ? 'bg-[var(--color-primary)]' : 'bg-[var(--color-border-strong)]')
            }
          >
            <span className={'absolute top-0.5 w-4 h-4 rounded-full bg-white transition-all ' + (m.enabled ? 'left-[18px]' : 'left-0.5')} />
          </button>
        </div>
      ))}
    </div>
  );
}
