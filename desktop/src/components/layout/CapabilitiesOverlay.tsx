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
 *   • CONNECTIONS — a CONFIG REGISTRY projection of GET /api/mcp: "Enabled" (available
 *                   for a session to use) vs "Off" (disabled), with an in-place toggle
 *                   and a faint load-timing tier marker (bolt = always / bedtime = on-demand / cell_tower =
 *                   channel). It is NOT a session-liveness view: `enabled` = "a session
 *                   CAN load this", not "a session IS connected right now" — liveness is
 *                   out of scope BY DESIGN (per-session runtime state, not this global
 *                   registry's job — run_3989a574). Raw connectionType/config JSON lives
 *                   in Settings' advanced path; this view answers "is this tool enabled,
 *                   and when does a session load it?".
 *
 * SCANNABLE SIGNALS (run_a85e6641): each skill row carries a HEALTH dot (the one standout —
 * green healthy / red low_success / amber stale / grey never_used) and a FAINT tier marker (bolt always /
 * bedtime lazy, deliberately muted so it never competes with the dot). Health is LAZY-loaded in a
 * SEPARATE effect from the skill list and is FAIL-SAFE: a rejected/slow /api/skills/health
 * leaves rows fully rendered + clickable with NO dot — it never blocks or crashes the list.
 * MCP rows carry an honest enabled/off dot + a faint tier marker — NO auth/liveness state is
 * fabricated (ConfigEntry has none, by design). never_used dots surface dead skills as a retire signal.
 *
 * DETAIL DRAWER: clicking a skill row opens an absolute right-side drawer (layered,
 * NOT a flex sibling — the list never compresses). It carries the plain description,
 * the trigger phrase, and THEN the demoted dev metadata (load-tier / version / health
 * detail incl. success-rate + last-used / folder id).
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
import { useCallback, useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { overlayQuery, invalidateOverlay } from './overlayQueries';
import { OverlayError } from './OverlayState';
import { mcpConfigService, type ConfigEntry } from '../../services/mcpConfig';
import { classifyLoadError } from '../../services/api';
import type { Skill, SkillHealthMap, SkillHealthStatus, ExpertsByDomain } from '../../types';
import { WorkbenchToolbar, OverlayDrawer } from './overlayShell';

/** Health dot presentation — the ONE scannable standout (Von Restorff). Qualitative only;
 *  no raw counts on the row (R30#4). Keyed by the qualitative status the backend folds. */
/** Human-readable domain headers for the Experts view (R2). Falls back to the raw
 *  domain key for any domain not listed, so a new expert domain never renders blank. */
const DOMAIN_LABEL: Record<string, string> = {
  code: 'Code Review',
  content: 'Content',
};

const HEALTH_DOT: Record<SkillHealthStatus, { color: string; label: string }> = {
  healthy: { color: 'var(--color-success, #4ade80)', label: 'Healthy — recently used, succeeding' },
  low_success: { color: 'var(--color-danger, #f87171)', label: 'Low success rate' },
  stale: { color: 'var(--color-warning, #fbbf24)', label: 'Stale — not used recently' },
  never_used: { color: 'var(--color-border-strong)', label: 'Never used — candidate to retire' },
};

/** Human-readable status word for the on-card health line. */
const STATUS_LABEL: Record<SkillHealthStatus, string> = {
  healthy: 'healthy',
  low_success: 'low success',
  stale: 'stale',
  never_used: 'never used',
};

/** Faint tier marker — deliberately muted so it does NOT compete with the health dot. */
const TIER_MARK: Record<'always' | 'lazy', { icon: string; label: string }> = {
  always: { icon: 'bolt', label: 'Always loaded' },
  lazy: { icon: 'bedtime', label: 'Lazy — loaded on use' },
};

/** MCP load-timing marker (distinct from the skill always/lazy map — MCP tiers are
 *  always/channel/ondemand). Faint by design: it says WHEN a session loads the MCP, it
 *  is NOT a live-connection state (this panel is a config registry, not a liveness probe). */
const MCP_TIER_MARK: Record<'always' | 'channel' | 'ondemand', { icon: string; label: string }> = {
  always: { icon: 'bolt', label: 'Always — loads at session start' },
  ondemand: { icon: 'bedtime', label: 'On-demand — loads when a session needs it' },
  channel: { icon: 'cell_tower', label: 'Channel — loads in channel sessions' },
};

export interface CapabilitiesContentProps {
  /** Land a prompt into a chat tab (used by "Teach Swarm a new skill"). Returns true
   *  if it landed (→ host closes the overlay). */
  onDispatch: (prompt: string) => boolean;
  /** Host-owned close. */
  close: () => void;
}

type ViewMode = 'skills' | 'connections' | 'experts';

/** The two signature abilities — rendered as heroes, the ONE thing that stands out.
 *  Keys are the exact folder_name (hyphenated, s_ prefix) — matched by `in SIGNATURE`. */
const SIGNATURE: Record<string, { icon: string; blurb: string }> = {
  's_autonomous-pipeline': { icon: 'rocket_launch', blurb: 'One sentence in → PR-ready code out. The delivery engine.' },
  's_pollinate': { icon: 'local_florist', blurb: 'Your message → poster, video, narrative — the right media, auto.' },
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

/** Raw frequency of a skill, or -1 when absent/never-used/no-data (so it sorts BELOW any
 *  used skill and, when health is empty, ALL skills tie at -1 → the tiebreak = pure name asc,
 *  identical to the old alphabetical order = zero jitter before health settles). run_ff4adc88. */
function freqOf(folderName: string, health: SkillHealthMap): number {
  const c = health[folderName]?.invocation_count;
  return typeof c === 'number' ? c : -1;
}

/** Within-group / strip comparator: invocation_count DESC, ties broken by name ASC
 *  (Gate-1 #4 — a deterministic tiebreak so equal-frequency cards never jitter between
 *  renders; never_used/no-data → freq -1 → sinks last). Curried so `.sort(byFrequencyThenName(h))`. */
export function byFrequencyThenName(health: SkillHealthMap) {
  return (a: Skill, b: Skill): number => {
    const d = freqOf(b.folderName, health) - freqOf(a.folderName, health);
    return d !== 0 ? d : a.name.localeCompare(b.name);
  };
}

/** The Most-Used strip membership (AC2): Top-`cap` skills by invocation_count across ALL
 *  categories, EXCLUDING heroes (shown above) and never_used/no-data (freq < 0). Pure.
 *  Returns [] when health is empty → the strip simply doesn't render until health settles
 *  (no flash of dead/mis-ranked skills). run_ff4adc88. */
export function mostUsed(skills: Skill[], health: SkillHealthMap, cap: number): Skill[] {
  return skills
    .filter((s) => !(s.folderName in SIGNATURE) && freqOf(s.folderName, health) >= 0)
    .sort(byFrequencyThenName(health))
    .slice(0, cap);
}

/** Group visible skills by category. Signature skills are pulled OUT into heroes so
 *  they don't also appear as a plain row. Within-group sort is ALPHABETICAL by name — a
 *  category group's job is FINDABILITY ("where do I find X"), which needs a PREDICTABLE
 *  position that doesn't shift with usage. Frequency ranking lives ONLY in the Most-Used
 *  strip ("what do I use") — the two surfaces do one job each and don't overlap
 *  (run_54491b88). Pure — safe on [] (renders no groups). */
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

// stable empties: a fresh [] / {} per render would re-run every memo below
const EMPTY_SKILLS: Skill[] = [];
const EMPTY_MCPS: ConfigEntry[] = [];
const EMPTY_HEALTH: SkillHealthMap = {};
const EMPTY_EXPERTS: ExpertsByDomain = {};

export function CapabilitiesContent({ onDispatch, close }: CapabilitiesContentProps) {
  const [view, setView] = useState<ViewMode>('skills');
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<Skill | null>(null);
  // Skills + MCPs, health and experts through the shared overlay queries
  // (overlayQueries): a reopen inside the freshness window or after a nav hover shows
  // the cached panel at once. Health and experts stay LAZY + FAIL-SAFE + independent:
  // their failure never blocks or crashes the list; `*Settled` distinguishes "not
  // fetched yet" (a loading placeholder) from "fetched — empty or failed" (nothing).
  const qc = useQueryClient();
  const mainQ = useQuery(overlayQuery.capabilities());
  const skills: Skill[] = mainQ.data?.skills ?? EMPTY_SKILLS;
  const mcps: ConfigEntry[] = mainQ.data?.mcps ?? EMPTY_MCPS;
  const loading = mainQ.isFetching;
  const error = mainQ.isError && !mainQ.data ? classifyLoadError(mainQ.error, 'Capabilities') : null;
  const setMcps = useCallback((f: (prev: ConfigEntry[]) => ConfigEntry[]) => {
    qc.setQueryData(overlayQuery.capabilities().queryKey,
      (d: { skills: Skill[]; mcps: ConfigEntry[] } | undefined) => d && { ...d, mcps: f(d.mcps) });
  }, [qc]);
  const load = useCallback(() => invalidateOverlay(qc, 'capabilities'), [qc]);
  const healthQ = useQuery(overlayQuery.capabilitiesHealth());
  const health: SkillHealthMap = healthQ.data ?? EMPTY_HEALTH;
  const healthSettled = !healthQ.isPending;
  const expertsQ = useQuery(overlayQuery.capabilitiesExperts());
  const experts: ExpertsByDomain = expertsQ.data ?? EMPTY_EXPERTS;
  const expertsSettled = !expertsQ.isPending;

  // The full health LINE shown ON each card by default (lazy: renders once health resolves;
  // absent health → a muted placeholder so the card layout never jumps). Format:
  //   ● healthy · 92% success · last used 2026-08-06
  // Qualitative status + the two detail facts the user asked to see up-front — never in a
  // drawer. never_used / no-data shows only the status word (no fabricated %/date, R30#4).
  const healthLineFor = useCallback((s: Skill, idSuffix: string = s.folderName) => {
    const h = health[s.folderName];
    if (!h || !(h.status in HEALTH_DOT)) {
      // Not-yet-fetched → a loading placeholder (keeps card height stable, lazy).
      // SETTLED-but-absent (empty/failed fetch, or a skill with genuinely no data) →
      // render NOTHING, never a perpetual 'loading…' (adversarial HIGH: {} on reject or
      // empty-table must not leave every card stuck loading). mt-auto bottom-aligns the
      // line across a row so cards with 1- vs 2-line descriptions stay aligned.
      if (healthSettled) return null;
      return (
        <div
          data-testid={`cap-healthline-${idSuffix}`}
          className="mt-auto pt-2 flex items-center gap-1.5 text-meta text-[var(--color-text-faint)] opacity-60"
        >
          <span className="inline-block w-2 h-2 rounded-full bg-[var(--color-border-strong)] shrink-0" />
          <span>loading health…</span>
        </div>
      );
    }
    const dot = HEALTH_DOT[h.status];
    const label = STATUS_LABEL[h.status];
    const parts: string[] = [];
    if (typeof h.success_rate === 'number') parts.push(`${Math.round(h.success_rate * 100)}% success`);
    if (h.last_used) parts.push(`last used ${h.last_used}`);
    return (
      <div
        data-testid={`cap-healthline-${idSuffix}`}
        data-status={h.status}
        className="mt-auto pt-2 flex items-center gap-1.5 text-meta text-[var(--color-text-muted)]"
      >
        <span
          className="inline-block w-2 h-2 rounded-full shrink-0"
          style={{ background: dot.color }}
          title={dot.label}
        />
        <span className="truncate">
          <span className="">{label}</span>
          {parts.length > 0 && <span className="text-[var(--color-text-faint)]"> · {parts.join(' · ')}</span>}
        </span>
      </div>
    );
  }, [health, healthSettled]);

  // Shared ordinary-skill card — rendered by BOTH the Most-Used strip and the category
  // groups (one renderer, no duplication — R25). A skill in the strip ALSO appears in its
  // category group (intentional dual-show: strip=shortcut, groups=full taxonomy — same
  // rationale as heroes). `scope` namespaces the testid so the two instances stay
  // individually addressable (no ambiguous duplicate testid). Heroes use their own card.
  const skillCard = useCallback((s: Skill, scope: 'strip' | 'group' = 'group') => {
    const idSuffix = scope === 'strip' ? `strip-${s.folderName}` : s.folderName;
    return (
      <button
        key={idSuffix}
        data-testid={`cap-skill-${idSuffix}`}
        onClick={() => setSelected(s)}
        className="text-left rounded-xl border border-[var(--color-border)] p-3 hover:bg-[var(--color-hover)] hover:border-[var(--color-border-strong)] min-w-0 flex flex-col"
      >
        {/* Title row: name + faint tier marker (muted, top-right) */}
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-[var(--color-text)] text-body font-semibold truncate">{s.name}</span>
          <span
            data-testid={`cap-tier-${idSuffix}`}
            data-tier={s.tier}
            title={TIER_MARK[s.tier]?.label ?? s.tier}
            aria-label={TIER_MARK[s.tier]?.label ?? s.tier}
            className="ml-auto shrink-0 material-symbols-outlined icon-xs text-[var(--color-text-faint)] opacity-50"
          >
            {TIER_MARK[s.tier]?.icon ?? ''}
          </span>
        </div>
        {/* One-line plain description */}
        <div className="text-small text-[var(--color-text-muted)] mt-1 line-clamp-2 min-w-0">{s.description}</div>
        {/* Health line — DEFAULT on every card (lazy: placeholder until health resolves) */}
        {healthLineFor(s, idSuffix)}
      </button>
    );
  }, [healthLineFor]);

  // Search filters the visible skill set; grouping + hero extraction happen after.
  const visibleSkills = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return skills;
    return skills.filter(
      (s) => s.name.toLowerCase().includes(q) || s.description.toLowerCase().includes(q) || s.category.toLowerCase().includes(q),
    );
  }, [skills, query]);

  // Groups are alphabetical (findability) — independent of health, so this memo does NOT
  // depend on `health` and never re-sorts when usage data lands. Frequency lives in the strip.
  const { heroes, groups } = useMemo(() => groupSkills(visibleSkills), [visibleSkills]);
  // Most-Used strip: Top 8 by frequency across all categories (excl. heroes + never_used).
  // Empty until health settles → the strip simply doesn't render (no flash). AC2.
  const topUsed = useMemo(() => mostUsed(visibleSkills, health, 8), [visibleSkills, health]);

  // enabledMcps = registry-enabled (a session CAN load it), NOT "connected now" — this
  // panel is a config registry, not a liveness view (run_3989a574). offMcps = disabled
  // catalog entries the user can flip on.
  const enabledMcps = useMemo(() => mcps.filter((m) => m.enabled), [mcps]);
  const offMcps = useMemo(() => mcps.filter((m) => !m.enabled && m.layer === 'catalog'), [mcps]);

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
  }, [load, setMcps]);

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
            {(['skills', 'experts', 'connections'] as ViewMode[]).map((v) => (
              <button
                key={v}
                role="radio"
                aria-checked={view === v}
                data-testid={`cap-view-${v}`}
                onClick={() => setView(v)}
                className={
                  'px-3 py-1 text-body rounded-md ' +
                  (view === v
                    ? 'bg-[color-mix(in_srgb,var(--color-primary)_15%,transparent)] text-[var(--color-primary)] font-semibold '
                    : 'text-[var(--color-text-muted)] hover:bg-[var(--color-hover)]')
                }
              >
                {v === 'skills' ? 'Skills' : v === 'experts' ? 'Experts' : 'Connections'}
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
              className="w-64 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-md px-3 py-1 text-body text-[var(--color-text)] placeholder:text-[var(--color-text-faint)]"
            />
          ) : null
        }
      />

      <div className="flex-1 overflow-y-auto px-6 py-4">
        {error && (
          <OverlayError testid="cap-error" retryTestid="cap-retry" message={error} onRetry={() => { void mainQ.refetch(); }} />
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
                    className="text-left rounded-xl border border-[var(--color-border-strong)] p-4 hover:bg-[var(--color-hover)] flex flex-col"
                    // Token-driven tint: the host sets --panel-accent to this overlay's
                    // zone color (Work/teal); color-mix keeps it theme-consistent (no
                    // hardcoded hex — navcard/overlay standard §1). Fallback to primary.
                    style={{ background: 'linear-gradient(135deg, color-mix(in srgb, var(--panel-accent, var(--color-primary)) 12%, transparent), transparent)' }}
                    data-testid={`cap-skill-${h.folderName}`}
                  >
                    <div className="text-meta tabular-nums tracking-wider text-[var(--color-primary)]">SIGNATURE</div>
                    <div className="text-title font-semibold mt-2 flex items-center gap-2">
                      <span className="material-symbols-outlined icon-md text-[var(--color-primary)]">{SIGNATURE[h.folderName]?.icon ?? 'auto_awesome'}</span>{h.name}
                    </div>
                    <div className="text-body text-[var(--color-text-muted)] mt-1">
                      {SIGNATURE[h.folderName]?.blurb ?? h.description}
                    </div>
                    {/* Health line ON heroes too — the user asked for it on EVERY card */}
                    {healthLineFor(h)}
                  </button>
                ))}
              </div>
            )}

            {/* Most-Used strip — Top skills by frequency, across all categories. Lighter than
                heroes (no gradient, muted header) so heroes stay the single dominant element
                (design-judgment check 4). Renders only when health has settled with data
                (empty until then → no flash). AC2. */}
            {topUsed.length > 0 && (
              <div className="mb-8" data-testid="cap-most-used">
                <h2 className="text-small font-semibold text-[var(--color-text-muted)] uppercase tracking-wide mb-1">
                  Most used
                </h2>
                <div className="grid grid-cols-2 gap-3">
                  {topUsed.map((s) => skillCard(s, 'strip'))}
                </div>
              </div>
            )}

            {/* Category groups — no empty group ever renders (§5). Between-group spacing
                (mb-8) intentionally exceeds within-group (gap-3) so grouping reads correctly
                (design-judgment: space within < space between). No per-group count header —
                the card grid already shows N (redundant data-ink removed, Tufte / check 2). */}
            {groups.map(([cat, list]) => (
              <div key={cat} className="mb-8" data-testid={`cap-group-${cat}`}>
                <h2 className="text-small font-semibold text-[var(--color-text-muted)] uppercase tracking-wide mb-1">
                  {cat}
                </h2>
                <div className="grid grid-cols-2 gap-3">
                  {list.map((s) => skillCard(s))}
                </div>
              </div>
            ))}

            {!loading && groups.length === 0 && heroes.length === 0 && (
              <div className="text-body text-[var(--color-text-faint)] py-8 text-center" data-testid="cap-empty">
                {query ? 'No abilities match your search.' : 'No abilities found.'}
              </div>
            )}
          </div>
        )}

        {!error && view === 'experts' && (
          <div data-testid="cap-experts-view">
            {/* Experts grouped by domain (chunking — Miller/Hick). Each card answers
                "what does this reviewer check?" (scope), not a field dump: title =
                English name, zh name demoted, scope is the one-line decision, and the
                ONE standout signal is health (isValid). No per-group count header
                (the grid shows N — redundant data-ink, Tufte). Between-group mb-8 >
                within-group gap-3 so grouping reads (design-judgment check 6). */}
            {Object.entries(experts)
              .sort(([a], [b]) => a.localeCompare(b))
              .map(([domain, list]) => (
                <div key={domain} className="mb-8" data-testid={`cap-expert-group-${domain}`}>
                  <h2 className="text-small font-semibold text-[var(--color-text-muted)] uppercase tracking-wide mb-1">
                    {DOMAIN_LABEL[domain] ?? domain}
                  </h2>
                  <div className="grid grid-cols-2 gap-3">
                    {list.map((e) => (
                      <div
                        key={e.expertId}
                        data-testid={`cap-expert-${e.expertId}`}
                        className="rounded-xl border border-[var(--color-border)] p-3 min-w-0 flex flex-col"
                      >
                        {/* Title: English name + zh demoted (muted, right) */}
                        <div className="flex items-center gap-2 min-w-0">
                          <span className="text-[var(--color-text)] text-body font-semibold truncate">{e.displayNameEn}</span>
                          <span className="ml-auto shrink-0 text-meta text-[var(--color-text-faint)] truncate">{e.displayNameZh}</span>
                        </div>
                        {/* Scope — the one-line "what this expert reviews" */}
                        <div className="text-small text-[var(--color-text-muted)] mt-1 line-clamp-2 min-w-0">{e.scope}</div>
                        {/* Health — the ONE standout signal: an unhealthy expert pops red */}
                        <div
                          data-testid={`cap-expert-health-${e.expertId}`}
                          data-valid={e.isValid}
                          className="mt-auto pt-2 flex items-center gap-1.5 text-meta text-[var(--color-text-muted)]"
                        >
                          <span
                            className="inline-block w-2 h-2 rounded-full shrink-0"
                            style={{ background: e.isValid ? 'var(--color-success, #4ade80)' : 'var(--color-danger, #f87171)' }}
                            title={e.isValid ? 'well-formed' : 'malformed frontmatter'}
                          />
                          <span className="truncate">
                            <span className="">{e.isValid ? 'ready' : 'malformed'}</span>
                          </span>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            {expertsSettled && Object.keys(experts).length === 0 && (
              <div className="text-body text-[var(--color-text-faint)] py-8 text-center" data-testid="cap-experts-empty">
                No experts found.
              </div>
            )}
          </div>
        )}

        {!error && view === 'connections' && (
          <div data-testid="cap-connections-view">
            <ConnGroup title="Enabled" tag={`${enabledMcps.length} available`} entries={enabledMcps} onToggle={toggleMcp} statusLabel="ENABLED" statusClass="text-[var(--color-primary)]" />
            <ConnGroup title="Off — one click to enable" tag={`${offMcps.length}`} entries={offMcps} onToggle={toggleMcp} statusLabel="OFF" statusClass="text-[var(--color-text-muted)]" />
            {!loading && enabledMcps.length === 0 && offMcps.length === 0 && (
              <div className="text-body text-[var(--color-text-faint)] py-8 text-center">No connections configured.</div>
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
            className="btn-lg btn-primary"
          >
            <span className="material-symbols-outlined icon-md">auto_awesome</span> Teach Swarm a new skill
          </button>
        ) : (
          <span className="text-meta text-[var(--color-text-faint)]">
            Enabled = available for a session to use. Bolt: loads at session start · moon: loads on demand · tower: in channel sessions. Toggle to enable/disable.
          </span>
        )}
      </div>

      {/* Detail drawer — dev metadata demoted here, off the row */}
      {selected && (
        <div className="absolute inset-0 z-[9]" onClick={() => setSelected(null)}>
          <OverlayDrawer widthPx={380} testid="cap-detail-drawer">
            <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--color-border)]">
              <div className="text-body font-semibold">{selected.name}</div>
              <button onClick={() => setSelected(null)} className="text-[var(--color-text-faint)] hover:text-[var(--color-text)]">✕</button>
            </div>
            <div className="p-4 space-y-4 overflow-y-auto text-body">
              <p className="text-[var(--color-text-muted)]">{selected.description}</p>
              <div>
                <div className="text-meta uppercase tracking-wide text-[var(--color-text-faint)] mb-1">How to use</div>
                <div className="text-body">Say what you want in chat — Swarm invokes it automatically.</div>
              </div>
              {/* Demoted dev metadata */}
              <div className="pt-3 border-t border-[var(--color-border)] text-small text-[var(--color-text-faint)] tabular-nums space-y-1">
                <div>source: {selected.sourceTier}</div>
                <div>version: {selected.version}</div>
                <div>load: {selected.tier}{selected.tier === 'always' ? ' (at startup)' : ' (on use)'}</div>
                {health[selected.folderName] && (
                  <div>health: {health[selected.folderName].status}
                    {typeof health[selected.folderName].success_rate === 'number'
                      ? ` · ${Math.round((health[selected.folderName].success_rate as number) * 100)}% success`
                      : ''}
                    {health[selected.folderName].last_used ? ` · last used ${health[selected.folderName].last_used}` : ''}
                  </div>
                )}
                <div>id: {selected.folderName}</div>
                {selected.visibility === 'internal' && <div className="text-[var(--color-text-muted)]">internal (owner-only)</div>}
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
      <h2 className="text-small font-semibold text-[var(--color-text-muted)] uppercase tracking-wide mb-1 flex items-baseline gap-2">
        {title}
        <span className="text-meta tabular-nums text-[var(--color-text-faint)]">{tag}</span>
      </h2>
      {entries.map((m) => (
        <div key={m.id} className="flex items-center gap-3 px-2 py-3 rounded-md hover:bg-[var(--color-hover)]">
          {/* Honest status dot: enabled=green / off=muted. This is a CONFIG registry —
              the dot means "enabled (available for a session to use)", NOT a live
              connection. ConfigEntry carries no runtime/auth signal, so none is
              fabricated (R30#4); liveness is out of scope by design (XG, run_3989a574). */}
          <span
            data-testid={`cap-conn-dot-${m.id}`}
            data-status={m.enabled ? 'enabled' : 'off'}
            aria-label={m.enabled ? 'enabled' : 'off'}
            className="inline-block w-2 h-2 rounded-full shrink-0"
            style={{ background: m.enabled ? 'var(--color-success, #4ade80)' : 'var(--color-border-strong)' }}
          />
          <div className="min-w-0">
            <div className="flex items-center gap-1.5 min-w-0">
              <span className="text-body truncate">{m.name}</span>
              {/* Faint load-timing marker — muted so it never competes with the toggle/name
                  (design-judgment check 4). Says WHEN a session loads it, not that it is live. */}
              <span
                data-testid={`cap-conn-tier-${m.id}`}
                data-tier={m.tier}
                title={MCP_TIER_MARK[m.tier]?.label ?? m.tier}
                aria-label={MCP_TIER_MARK[m.tier]?.label ?? m.tier}
                className="shrink-0 material-symbols-outlined icon-xs text-[var(--color-text-faint)] opacity-50"
              >
                {MCP_TIER_MARK[m.tier]?.icon ?? ''}
              </span>
            </div>
            {m.description && <div className="text-small text-[var(--color-text-faint)] truncate">{m.description}</div>}
          </div>
          <div className="flex-1" />
          <span className={`text-meta tabular-nums ${statusClass}`}>{statusLabel}</span>
          <button
            data-testid={`cap-toggle-${m.id}`}
            onClick={() => onToggle(m)}
            aria-label={`toggle ${m.name}`}
            role="switch"
            aria-checked={m.enabled}
            className={
              'w-9 h-5 rounded-full relative transition-colors ' +
              (m.enabled ? 'bg-[var(--color-primary)]' : 'bg-[var(--color-border-strong)]')
            }
          >
            <span className={'absolute top-0.5 w-4 h-4 rounded-full bg-[var(--color-on-accent)] transition-all ' + (m.enabled ? 'left-[18px]' : 'left-0.5')} />
          </button>
        </div>
      ))}
    </div>
  );
}
